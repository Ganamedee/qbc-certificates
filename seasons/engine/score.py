"""The seasons workflow's score job: opens every new or edited season entry and grades it.

    python -m seasons.engine.score --repo OWNER/NAME --event "$GITHUB_EVENT_PATH" --out results.json

This job holds SEASONS_SEED and a read-only token. It never prints, comments on or writes an entry: each card is
opened here and graded by child.py in a clean process, and results.json carries only each entry's issue, author
and scores. The post job, which can write but never sees the secret, checks them against GitHub before it posts.

Season 3 gives every account a case of its own, made from a seed this job derives for it (cases.py), which goes to
child.py on stdin and nowhere else. results.json carries the case as it's published, and a score, but never that
seed, which file holds which flaw, or anyone's calls.
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

from . import cases, forms, kinds, seal
from .config import LOGIN, OFF_BOARD, OFF_LABEL, SEASONS, Season, season_of
from .gh import GitHub, GitHubError

CHILD = Path(__file__).resolve().parent / "child.py"
CHILD_SECONDS = 180
CHILD_MEMORY = 3 * 1024 ** 3
SWEEP_DAYS = 14      # a scheduled run also looks at every issue changed in this many days
CASE_OPENS_PER_RUN = 30     # Season 3 cases one run opens: any more are left for the next run


def taken_off(body: str | None) -> bool:
    """Whether the entry's author has taken it off the board: a line of the body is OFF_BOARD, give or take spaces."""
    return any(line.strip() == OFF_BOARD for line in (body or "").split("\n"))


def unmarked(body: str | None) -> str:
    """The body as an entry's fingerprint reads it: without its off-the-board lines or trailing whitespace. The club
    site takes an entry off by adding a blank line and OFF_BOARD at the end, and puts it back by deleting every such
    line and the trailing whitespace, so the entry reads the same off the board, back on it and as first sent."""
    return "\n".join(line for line in (body or "").split("\n") if line.strip() != OFF_BOARD).rstrip()


def body_sha(body: str | None) -> str:
    """The entry's fingerprint: the same for an entry off the board and back on it, so it keeps its score and time."""
    return hashlib.sha256(unmarked(body).encode("utf-8")).hexdigest()


def fingerprints(body: str | None) -> set:
    """Every fingerprint the state may hold for an entry with this body: body_sha, and the one the workflow took
    before the off-the-board line, of the whole body as it was then (the issue form ends a body with a newline)."""
    text = unmarked(body)
    return {body_sha(body), *(hashlib.sha256(raw.encode("utf-8")).hexdigest()
                              for raw in (body or "", text + "\n", text + "\r\n"))}


def grader_sha(root: Path, season: Season) -> str:
    """A fingerprint of the season's grader and holes: when it changes, every entry is scored again."""
    folder = Path(root) / season.grader
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def withdrawn(issue: dict) -> bool:
    """Closed as not planned: by the workflow for its off-the-board line (it has OFF_LABEL), so off until the line
    goes, or by a person, so off until it's reopened."""
    return issue.get("state") == "closed" and issue.get("state_reason") == "not_planned"


def off_label(issue: dict) -> bool:
    return OFF_LABEL in {(label or {}).get("name") for label in issue.get("labels") or []}


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


def open_sealed(season: Season, text: str, private: bytes, base: dict) -> tuple:
    """(the card's payload, None) if it opens and is this season's card for this entry's author, else (None, the
    problem result)."""
    try:
        entry = seal.open_card(text, private, season.number)
    except seal.SealError as error:
        return None, {**base, "outcome": "problem", "problem": "seal", "message": str(error)}
    if entry.get("season") != season.number:
        return None, {**base, "outcome": "problem", "problem": "wrong-season"}
    sealed_for = entry.get("github")
    if not isinstance(sealed_for, str) or sealed_for.casefold() != base["author"].casefold():
        return None, {**base, "outcome": "problem", "problem": "not-yours"}
    return entry, None


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
        entry, problem = open_sealed(season, text, private, base)
        if problem:
            return problem
    else:
        entry = {"text": text}
    kind = kinds.of(season)
    verdict = grade(season, root, entry)
    if not verdict.get("ok"):
        return {**base, "outcome": "problem", "problem": str(verdict.get("problem") or "crashed"),
                **{k: verdict[k] for k in kind.EXTRA if isinstance(verdict.get(k), str)}}
    return {**base, "outcome": "scored", **{k: verdict[k] for k in kind.FIELDS if k in verdict}}


def one_set(season: Season) -> bool:
    """Whether each account's first scored entry is its only one (Season 3)."""
    return bool(getattr(kinds.of(season), "ONE_SET", False))


def score_case(season: Season, issue: dict, private: bytes | None, key: bytes | None, root: Path,
               time: str | None, locks: dict, opens: dict) -> dict | None:
    """Season 3: an entry opens the builder's case (calls box empty) or holds their one set of calls (a card).
    locks: {account id: its lock}, from the state and from this run, since the first set of calls scored locks the
    account for good. opens: {"left": cases this run may still open}. None: left out, for the next run."""
    base = about(season, issue, time)
    author, account = base["author"], base["author_id"]
    if not LOGIN.fullmatch(author) or account < 1:
        return {**base, "outcome": "problem", "problem": "no-author"}
    fields = kinds.of(season).FIELDS
    lock = locks.get(str(account))
    if isinstance(lock, dict):
        if lock.get("issue") == base["issue"]:       # the scored entry itself: whatever it says now, its score stands
            return {**base, "outcome": "scored", "time": lock.get("time"), **{k: lock.get(k) for k in fields}}
        return {**base, "outcome": "locked", "scored_issue": lock.get("issue")}
    text = forms.fields(issue.get("body") or "").get(season.field, "")
    if not text.strip():
        if private is None:
            return {**base, "outcome": "waiting"}
        if opens["left"] < 1:
            return None
        opens["left"] -= 1
        verdict = grade(season, root, {"seed": cases.case_seed(key, account).hex(), "open": True, "login": author,
                                       "account": account})
        if not (verdict.get("ok") and isinstance(verdict.get("fingerprint"), str)
                and isinstance(verdict.get("case_text"), str)):
            return None                               # the next run tries again
        return {**base, "outcome": "case-open", "fingerprint": verdict["fingerprint"],
                "case_text": verdict["case_text"]}
    if private is None:
        return {**base, "outcome": "waiting"}
    entry, problem = open_sealed(season, text, private, base)
    if problem:
        return problem
    verdict = grade(season, root, {"seed": cases.case_seed(key, account).hex(), "case": entry.get("case"),
                                   "calls": entry.get("calls")})
    if not verdict.get("ok"):
        return {**base, "outcome": "problem", "problem": str(verdict.get("problem") or "crashed")}
    result = {**base, "outcome": "scored", **{k: verdict[k] for k in fields if k in verdict}}
    locks[str(account)] = {"issue": base["issue"], "author": author, "time": base["time"],
                           **{k: result.get(k) for k in fields}}
    return result


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
    key = cases.case_key(seed) if private else None      # Season 3's cases come from the same secret
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
        off = (known or {}).get("outcome") == "withdrawn"
        by_line = off and known.get("marker") is True     # the state has it taken off for its off-the-board line
        if withdrawn(issue) and not off_label(issue):     # closed as not planned by a person: off till it's reopened,
            if known and (not off or by_line):            # whatever its body says
                todo.append((season, issue, "closed"))
            continue
        if taken_off(issue.get("body")):
            if not (by_line and withdrawn(issue)):
                todo.append((season, issue, "marker"))
            continue
        # Anything else is scored unless it's the same entry as before, on the board as before. One the workflow
        # took off for its off-the-board line (it has OFF_LABEL) is put back now the line has gone.
        if (season.number not in full and known and not off and not off_label(issue)
                and known.get("body_sha") in fingerprints(issue.get("body"))):
            continue
        todo.append((season, issue, "score"))
    try:
        edited = gh.edited_times([issue["number"] for _, issue, _ in todo]) if todo else {}
    except GitHubError:
        edited = {}          # each entry then counts from its last update, which is never earlier than its edit

    def when(season, issue) -> str | None:
        """When the entry in the body was made: its last edit, or when it was opened. An entry the workflow already
        has keeps its time while its body is the same, so taking it off the board and back doesn't move it."""
        known = states[season.number]["issues"].get(str(issue["number"])) or {}
        if known.get("body_sha") in fingerprints(issue.get("body")) and isinstance(known.get("time"), str):
            return known["time"]
        return edited.get(issue["number"]) or (issue.get("created_at") if issue["number"] in edited else None)

    jobs = [(season, issue, job, when(season, issue)) for season, issue, job in todo]

    def order(item) -> tuple:     # Season 3's entries in the order they were made, so the first calls win
        season, issue, _, time = item
        if not one_set(season):
            return (0, "", 0)
        return (1, str(time or issue.get("updated_at") or issue.get("created_at") or ""), issue["number"])

    jobs.sort(key=order)          # stable: every other season's entries keep their order
    locks = {}
    for n, s in SEASONS.items():
        if one_set(s):
            held = states[n].get("locks")
            locks[n] = dict(held) if isinstance(held, dict) else {}
    opens = {"left": CASE_OPENS_PER_RUN}
    results = []
    for season, issue, job, time in jobs:
        if job in ("marker", "closed"):
            results.append({**about(season, issue, time), "outcome": "withdrawn", "marker": job == "marker"})
            continue
        try:
            if season.number in locks:
                result = score_case(season, issue, private, key, root, time, locks[season.number], opens)
                if result is not None:
                    results.append(result)
            else:
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
