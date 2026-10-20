"""The seasons workflow's score job: opens every new or edited season entry and grades it.

    python -m seasons.engine.score --repo OWNER/NAME --event "$GITHUB_EVENT_PATH" --out results.json

This job holds SEASONS_SEED and a read-only token. It never prints, comments on or writes an entry: each card is
opened here and graded by child.py in a clean process, and results.json carries only each entry's issue, author
and scores. The post job, which can write but never sees the secret, checks them against GitHub before it posts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import forms, seal
from .config import LOGIN, SEASONS, Season, season_of
from .gh import GitHub, GitHubError

CHILD = Path(__file__).resolve().parent / "child.py"
CHILD_SECONDS = 180
CHILD_MEMORY = 3 * 1024 ** 3
SWEEP_DAYS = 14      # a scheduled run also looks at every issue changed in this many days


def body_sha(body: str | None) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def grader_sha(root: Path, season: Season) -> str:
    """A fingerprint of the season's grader and holes: when it changes, every entry is scored again."""
    folder = Path(root) / season.grader
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def withdrawn(issue: dict) -> bool:
    """Closed as not planned, by its author or a moderator: off the board until it's reopened."""
    return issue.get("state") == "closed" and issue.get("state_reason") == "not_planned"


def load_state(root: Path, season: Season) -> dict:
    path = season.state_file(root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"v": 1, "season": season.number, "issues": {}, "waiting": {}}
    state.setdefault("issues", {})
    state.setdefault("waiting", {})
    return state


def _limits() -> None:  # runs in the child before it starts
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY, CHILD_MEMORY))
    resource.setrlimit(resource.RLIMIT_CPU, (CHILD_SECONDS, CHILD_SECONDS))


def grade(season: Season, root: Path, entry: dict) -> dict:
    """The child's verdict on an opened entry: scores, or a problem code. Never raises."""
    with tempfile.TemporaryDirectory() as home:
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"}
        try:
            done = subprocess.run(
                [sys.executable, "-I", str(CHILD), "--grader", str(root / season.grader), "--season",
                 str(season.number)],
                input=json.dumps(entry), capture_output=True, text=True, timeout=CHILD_SECONDS + 30, env=env,
                cwd=home, preexec_fn=_limits if os.name == "posix" else None)
        except subprocess.TimeoutExpired:
            return {"ok": False, "problem": "timeout"}
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    try:
        out = json.loads(lines[-1])
    except (IndexError, ValueError):
        return {"ok": False, "problem": "crashed"}
    return out if isinstance(out, dict) else {"ok": False, "problem": "crashed"}


def about(season: Season, issue: dict, time: str | None) -> dict:
    """What every result says about its issue, so the post job can check it's still the same entry."""
    return {"season": season.number, "issue": int(issue["number"]), "author": (issue.get("user") or {}).get("login")
            or "", "author_id": int((issue.get("user") or {}).get("id") or 0), "body_sha": body_sha(issue.get("body")),
            "time": time or issue.get("updated_at") or issue.get("created_at")}


def score_issue(season: Season, issue: dict, private: bytes | None, root: Path, time: str | None = None) -> dict:
    """time: when the entry in the body was made (its last edit, or when it was opened)."""
    base = about(season, issue, time)
    author = base["author"]
    if not LOGIN.fullmatch(author):
        return {**base, "outcome": "problem", "problem": "no-author"}
    text = forms.fields(issue.get("body") or "").get(season.field, "")
    if not text.strip():
        return {**base, "outcome": "problem", "problem": "no-card"}
    if season.sealed:
        if private is None:
            return {**base, "outcome": "waiting"}
        try:
            entry = seal.open_card(text, private, season.number)
        except seal.SealError as error:
            return {**base, "outcome": "problem", "problem": "seal", "message": str(error)}
        if entry.get("season") != season.number:
            return {**base, "outcome": "problem", "problem": "wrong-season"}
        sealed_for = entry.get("github")
        if not isinstance(sealed_for, str) or sealed_for.casefold() != author.casefold():
            return {**base, "outcome": "problem", "problem": "not-yours"}
    else:
        entry = {"text": text}
    verdict = grade(season, root, entry)
    if not verdict.get("ok"):
        return {**base, "outcome": "problem", "problem": str(verdict.get("problem") or "crashed")}
    scores = {k: verdict[k] for k in ("total", "par", "solved", "holes", "notes", "card") if k in verdict}
    return {**base, "outcome": "scored", **scores}


def candidates(gh, event: dict, now: datetime, full: set) -> dict:
    """{issue number: issue} for every issue that may be a season entry: the open ones with a season label, the
    one this run is for, everything changed in the last SWEEP_DAYS (so an entry whose run GitHub dropped from the
    queue is still scored) and, for a season whose grader changed, every entry it has ever had."""
    found = {}
    for season in SEASONS.values():
        for issue in gh.issues(label=season.label, state="all" if season.number in full else "open"):
            found[issue["number"]] = issue
    since = (now - timedelta(days=SWEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for issue in gh.issues(since=since):
        if season_of(issue.get("body")):
            found.setdefault(issue["number"], issue)
    number = (event.get("issue") or {}).get("number")
    if number and not (event.get("issue") or {}).get("pull_request"):
        found[number] = gh.issue(number)       # the body as it is now, not as the event saw it
    return found


def run(gh, root: Path, event: dict, seed: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        private = seal.private_key_from_seed(seed) if seed else None
    except seal.SealError:
        private = None
    states = {n: load_state(root, s) for n, s in SEASONS.items()}
    graders = {n: grader_sha(root, s) for n, s in SEASONS.items()}
    full = {n for n in SEASONS if states[n].get("grader") != graders[n]}
    todo = []
    for number, issue in sorted(candidates(gh, event, now, full).items()):
        season = season_of(issue.get("body"))
        if season is None:
            labelled = {(label or {}).get("name") for label in issue.get("labels") or []}
            season = next((s for s in SEASONS.values() if s.label in labelled), None)
            if season is None:
                continue
        known = states[season.number]["issues"].get(str(number))
        if withdrawn(issue):
            if known and known.get("outcome") != "withdrawn":
                todo.append((season, issue, "withdrawn"))
            continue
        if (season.number not in full and known and known.get("outcome") != "withdrawn"
                and known.get("body_sha") == body_sha(issue.get("body"))):
            continue
        todo.append((season, issue, "score"))
    try:
        edited = gh.edited_times([issue["number"] for _, issue, _ in todo]) if todo else {}
    except GitHubError:
        edited = {}          # each entry then counts from its last update, which is never earlier than its edit
    results = []
    for season, issue, job in todo:
        time = edited.get(issue["number"]) or (issue.get("created_at") if issue["number"] in edited else None)
        if job == "withdrawn":
            results.append({**about(season, issue, time), "outcome": "withdrawn"})
            continue
        try:
            results.append(score_issue(season, issue, private, root, time))
        except Exception:  # one strange entry must never stop the run, and its details stay out of the log
            results.append({**about(season, issue, time), "outcome": "problem", "problem": "crashed"})
    return {"v": 1, "repo": gh.repo, "key_line": seal.key_line(seal.public_key(private)) if private else None,
            "full": {str(n): graders[n] for n in sorted(full)}, "results": results}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="score new season entries")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--event", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--root", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)
    event = {}
    if args.event and args.event.exists():
        event = json.loads(args.event.read_text(encoding="utf-8"))
    data = run(GitHub(args.repo), args.root.resolve(), event, os.environ.get("SEASONS_SEED", ""))
    args.out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    counts = {}
    for r in data["results"]:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    print("Season entries:", ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "nothing new",
          "| season key:", "set" if data["key_line"] else "not set (SEASONS_SEED is missing or too short)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
