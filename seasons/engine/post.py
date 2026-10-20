"""The seasons workflow's post job: checks the score job's results against GitHub, then comments on, labels and
closes each entry and rewrites the live boards.

    python -m seasons.engine.post --repo OWNER/NAME --results results.json

This job can write to the repo and its issues, but it never sees SEASONS_SEED or an entry's circuits. It uses a
result only if every field is in range and the issue still has the author, account and body the score job saw,
and everything it posts is a score, a rank or a fixed message: never anything a member wrote.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import boards, seal
from .config import COMMENT_TAG, KEY_FILE, LOGIN, SEASONS, Season, season_of
from .gh import GitHub, GitHubError
from .score import body_sha, grader_sha, load_state, withdrawn

BOT = "github-actions[bot]"     # who the workflow's own token posts as
MAX_RESULTS = 5_000
MAX_STROKES = 100_000
WRITE_PAUSE = 1.0               # seconds after each write, well under GitHub's limit on new comments
LIST_ABOVE = 20                 # with more results than this, read the season's issues in pages, not one by one
TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
SHA = re.compile(r"[0-9a-f]{64}")
CARD = re.compile(r"[0-9A-F]{6}")
NOTES = {"wrong", "invalid", "unplayed", "crashed"}
PROBLEMS = {"no-author", "no-card", "seal", "wrong-season", "not-yours", "no-holes", "bad-holes", "bad-entry",
            "too-big", "timeout", "crashed"}
SEAL_TEXTS = {   # SealError messages that open_card can raise, the only ones repeated to a member
    "no sealed card found: paste the whole block, BEGIN and END lines included",
    "the sealed card is damaged: copy it again from your terminal",
    "the sealed card doesn't open: copy it again from your terminal, and seal it with the current pack",
    "the sealed card is too big to score",
    "this card can't be opened",
}
SEAL_SEASON = re.compile(r"this card is for Season \d{1,3}, not Season \d{1,3}")


# ---- checking the score job's results ----

def _int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def check(item, courses: dict) -> dict | None:
    """The result with only the fields this job uses, or None if anything is missing, odd or out of range."""
    if not isinstance(item, dict) or not _int(item.get("season"), 1, 255) or item["season"] not in SEASONS:
        return None
    if not (_int(item.get("issue"), 1, 10 ** 9) and isinstance(item.get("author"), str)
            and LOGIN.fullmatch(item["author"]) and _int(item.get("author_id"), 1, 10 ** 12)
            and isinstance(item.get("body_sha"), str) and SHA.fullmatch(item["body_sha"])
            and isinstance(item.get("time"), str) and TIME.fullmatch(item["time"])):
        return None
    out = {k: item[k] for k in ("season", "issue", "author", "author_id", "body_sha", "time")}
    outcome = item.get("outcome")
    if outcome in ("waiting", "withdrawn"):
        return {**out, "outcome": outcome}
    if outcome == "problem":
        code = item.get("problem")
        if code not in PROBLEMS:
            return None
        message = item.get("message") if code == "seal" else None
        if message is not None and not (message in SEAL_TEXTS or SEAL_SEASON.fullmatch(str(message))):
            message = None
        return {**out, "outcome": "problem", "problem": code, **({"message": message} if message else {})}
    if outcome != "scored":
        return None
    course = courses.get(item["season"]) or []
    pars = {str(n): p for n, _, p in course}
    holes, notes = item.get("holes"), item.get("notes")
    if not (pars and isinstance(holes, dict) and set(holes) == set(pars) and isinstance(notes, dict)
            and set(notes) <= set(pars) and all(v in NOTES for v in notes.values())):
        return None
    if not all(v is None or _int(v, 0, MAX_STROKES) for v in holes.values()):
        return None
    solved = sum(v is not None for v in holes.values())
    total = sum(v if v is not None else 2 * pars[k] for k, v in holes.items())
    if set(notes) != {k for k, v in holes.items() if v is None}:
        return None
    card = item.get("card")
    if not (item.get("total") == total and item.get("par") == sum(pars.values()) and item.get("solved") == solved
            and isinstance(card, str) and (card == "none" if solved == 0 else bool(CARD.fullmatch(card)))):
        return None
    return {**out, "outcome": "scored", "total": total, "par": item["par"], "solved": solved,
            "holes": {k: holes[k] for k in sorted(holes, key=int)}, "notes": dict(sorted(notes.items())),
            "card": card}


def season_for(issue: dict) -> Season | None:
    season = season_of(issue.get("body"))
    if season is None:
        labelled = {(label or {}).get("name") for label in issue.get("labels") or []}
        season = next((s for s in SEASONS.values() if s.label in labelled), None)
    return season


def same_entry(issue: dict | None, result: dict) -> bool:
    """The issue still has the author, account and body the score job saw, and is still that season's entry."""
    if not issue or "pull_request" in issue:
        return False
    user = issue.get("user") or {}
    season = season_for(issue)
    if not (user.get("login") == result["author"] and user.get("id") == result["author_id"]
            and body_sha(issue.get("body")) == result["body_sha"] and season and season.number == result["season"]):
        return False
    return withdrawn(issue) == (result["outcome"] == "withdrawn")


# ---- what the workflow says ----

def tag(season: Season, number: int) -> str:
    return f"{COMMENT_TAG} season={season.number} issue={number} -->"


def problem_text(season: Season, result: dict) -> str:
    command = season.command.format(login=result["author"])
    n = season.number
    code = result["problem"]
    if code == "seal":
        message = result.get("message") or "the sealed card doesn't open: copy it again from your terminal"
        return message[0].upper() + message[1:] + "."
    return {
        "no-author": "There's no GitHub account behind this entry, so it can't go on the board.",
        "no-card": f"The card box is empty. Run `{command}` in the Season {n} pack and paste the whole block it "
                   f"prints, BEGIN and END lines included.",
        "wrong-season": f"This card was made for a different season. Run `{command}` in the Season {n} pack and "
                        f"paste that card instead.",
        "not-yours": f"This card was sealed for a different GitHub account, and a card only counts for the "
                     f"account it names. Run `{command}` and paste that card.",
        "no-holes": "This card has no solved holes in it. Solve at least one, then make a new card.",
        "bad-holes": f"This card doesn't match Season {n}'s holes. Make a new card with the latest Season {n} pack.",
        "bad-entry": f"This card doesn't match Season {n}'s holes. Make a new card with the latest Season {n} pack.",
        "too-big": "One of the circuits in this card is too big to score. Shorten it, check it with "
                   "`python grade.py`, then make a new card.",
        "timeout": "Scoring this card took too long. Check every hole with `python grade.py`, then make a new card.",
        "crashed": "Something in this card stopped the scorer. Check every hole with `python grade.py`, then "
                   "make a new card.",
    }[code]


def comment_text(season: Season, result: dict, repo: str, place: int | None = None, best: dict | None = None,
                 count: int = 0) -> str:
    n, number = season.number, result["issue"]
    form = f"[Season {n} entry form]({season.form_url(repo)})"
    outcome = result["outcome"]
    if outcome == "waiting":
        lines = [f"{season.emoji} **Season {n}: your card is in.** Scoring switches on as soon as the club's season "
                 f"key is set up, and this comment will show your score then. There's nothing to do meanwhile."]
    elif outcome == "withdrawn":
        lines = [f"{season.emoji} **Season {n}: this entry is off the board** because the issue was closed as not "
                 f"planned. Reopen it to put it back."]
    elif outcome == "problem":
        lines = [f"{season.emoji} **Season {n}: this entry isn't scored yet.** {problem_text(season, result)}", "",
                 f"Then edit this issue and paste the new card in, or send it with the {form}. It's scored again "
                 f"within minutes."]
    else:
        diff = result["total"] - result["par"]
        holes = " · ".join(f"H{k} {v}" if v is not None else f"H{k} unsolved" for k, v in result["holes"].items())
        lines = [f"{season.emoji} **Season {n}, scored: {result['total']} strokes** (par {result['par']}, "
                 f"{diff:+d}), {result['solved']} of {len(result['holes'])} holes solved, card {result['card']}.",
                 "", holes, ""]
        board = f"[live board]({season.board_url(repo)})"
        if best and best["issue"] == number:
            lines.append(f"This is your best card: **{boards.ordinal(place)} of {count}** on the {board} right now.")
        elif best:
            lines.append(f"Your best card is still #{best['issue']}, with {best['total']} strokes: "
                         f"**{boards.ordinal(place)} of {count}** on the {board} right now.")
        command = season.command.format(login=result["author"])
        lines += ["", f"Beat it whenever you like: improve a hole, run `{command}` again and send the new card with "
                      f"the {form}, or edit this issue and paste it in. Your circuits stay sealed, and only your "
                      f"strokes are public."]
    return "\n".join(lines + ["", tag(season, number)])


# ---- doing it ----

def live_issues(gh, results: list) -> dict:
    """{issue number: the issue as it is now} for every result, read in as few calls as is sensible."""
    live = {}
    if len(results) > LIST_ABOVE:
        for number in sorted({r["season"] for r in results}):
            for issue in gh.issues(label=SEASONS[number].label):
                live[issue["number"]] = issue
    for r in results:
        if r["issue"] not in live:
            try:
                live[r["issue"]] = gh.issue(r["issue"])
            except GitHubError:
                live[r["issue"]] = None      # gone, or GitHub didn't answer: the next run looks again
    return live


def our_comment(gh, number: int, season: Season) -> int | None:
    """The workflow's own comment on an issue, if a run posted one but its state wasn't saved."""
    for c in gh.comments(number):
        if (c.get("user") or {}).get("login") == BOT and tag(season, number) in (c.get("body") or ""):
            return c.get("id")
    return None


def say(gh, number: int, season: Season, text: str, comment_id: int | None) -> int:
    """Edits the workflow's comment on the issue, or posts one. Returns its id."""
    if comment_id is None:
        comment_id = our_comment(gh, number, season)
    if comment_id is not None:
        try:
            gh.edit_comment(comment_id, text)
            gh.pause(WRITE_PAUSE)
            return comment_id
        except GitHubError as error:
            if "HTTP 404" not in str(error):      # 404: someone deleted it, so post a new one
                raise
    made = gh.comment(number, text)
    gh.pause(WRITE_PAUSE)
    return int(made.get("id") or 0) or None


SAME = ("body_sha", "outcome", "problem", "message", "total", "holes", "card", "time")


def run(gh, root: Path, data, now: datetime | None = None) -> Counter:
    now = now or datetime.now(timezone.utc)
    if not isinstance(data, dict) or data.get("v") != 1 or data.get("repo") != gh.repo:
        raise SystemExit("results.json isn't from this repo's score job")
    raw = data.get("results")
    if not isinstance(raw, list) or len(raw) > MAX_RESULTS:
        raise SystemExit("results.json has no list of results, or far too many")
    report = Counter()
    courses = {n: boards.holes(root, s) for n, s in SEASONS.items()}
    states = {n: load_state(root, s) for n, s in SEASONS.items()}
    changed = set()

    for number, sha in (data.get("full") or {}).items():         # every entry was scored again for this grader
        if str(number).isdigit() and int(number) in SEASONS and sha == grader_sha(root, SEASONS[int(number)]):
            states[int(number)]["grader"] = sha
            changed.add(int(number))

    results, seen = [], set()
    for item in raw:
        r = check(item, courses)
        if r is None or r["issue"] in seen:
            report["dropped"] += 1
            continue
        seen.add(r["issue"])
        results.append(r)
    live = live_issues(gh, results) if results else {}

    todo = []      # (season, result, issue, the state entry before this run)
    for r in results:
        issue = live.get(r["issue"])
        if not same_entry(issue, r):
            report["changed since scoring"] += 1
            continue
        season, key = SEASONS[r["season"]], str(r["issue"])
        state = states[season.number]
        known = state["issues"].get(key)
        if r["outcome"] == "waiting":
            if key in state["waiting"] and not known:
                continue                                  # already told it's waiting
            state["issues"].pop(key, None)
            todo.append((season, r, issue, known))
            changed.add(season.number)
            continue
        entry = {"author": r["author"], "author_id": r["author_id"], "body_sha": r["body_sha"], "time": r["time"],
                 "outcome": r["outcome"]}
        entry.update({k: r[k] for k in ("problem", "message", "total", "par", "solved", "holes", "notes", "card")
                      if k in r})
        comment_id = (known or {}).get("comment_id") or state["waiting"].get(key)
        if comment_id:
            entry["comment_id"] = comment_id
        if known and all(known.get(k) == entry.get(k) for k in SAME):
            continue                                      # scored again, same result: nothing to say
        state["issues"][key] = entry
        state["waiting"].pop(key, None)
        todo.append((season, r, issue, known))
        changed.add(season.number)

    tables = {n: boards.standings(states[n]) for n in changed}
    labelled = set()
    for season, r, issue, known in todo:
        state, key = states[season.number], str(r["issue"])
        try:
            if season.number not in labelled:
                gh.ensure_label(season.label, season.colour, f"An entry for {season.title}")
                labelled.add(season.number)
            place, best = boards.rank_of(tables[season.number], r["author_id"], r["author"])
            text = comment_text(season, r, gh.repo, place, best, len(tables[season.number]))
            if r["outcome"] == "waiting":
                state["waiting"][key] = say(gh, r["issue"], season, text, (known or {}).get("comment_id"))
            elif r["outcome"] == "withdrawn":
                if state["issues"][key].get("comment_id"):
                    say(gh, r["issue"], season, text, state["issues"][key]["comment_id"])
            else:
                state["issues"][key]["comment_id"] = say(gh, r["issue"], season, text,
                                                         state["issues"][key].get("comment_id"))
            if r["outcome"] != "withdrawn" and season.label not in {(x or {}).get("name") for x in
                                                                    issue.get("labels") or []}:
                gh.add_labels(r["issue"], [season.label])
                gh.pause(WRITE_PAUSE)
            if r["outcome"] == "scored" and issue.get("state") == "open":
                gh.set_state(r["issue"], "closed", "completed")
                gh.pause(WRITE_PAUSE)
            elif r["outcome"] == "problem" and issue.get("state") == "closed":
                gh.set_state(r["issue"], "open")      # an edit broke a scored card: open, so it's seen
                gh.pause(WRITE_PAUSE)
            report[r["outcome"]] += 1
        except GitHubError as error:
            print(f"Couldn't update #{r['issue']} ({error}): the next run tries again.", file=sys.stderr)
            if known:      # forget this run's result for it, so the next run scores and posts it again
                state["issues"][key] = known
            else:
                state["issues"].pop(key, None)
                state["waiting"].pop(key, None)
            report["failed"] += 1

    updated = now.strftime("%Y-%m-%d %H:%M UTC")
    for number, season in SEASONS.items():
        md, js = season.board(root, "md"), season.board(root, "json")
        if number in changed or not md.exists() or not js.exists():
            text, data_text = boards.KINDS[number](season, states[number], root, updated, season.form_url(gh.repo))
            md.parent.mkdir(parents=True, exist_ok=True)
            md.write_text(text, encoding="utf-8")
            js.write_text(data_text, encoding="utf-8")
        if number in changed:
            path = season.state_file(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(states[number], indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                            encoding="utf-8")

    line = data.get("key_line")
    if line:
        try:
            seal.parse_key_line(line)
        except seal.SealError:
            report["bad key line"] += 1
        else:
            path = Path(root) / KEY_FILE
            old = path.read_text(encoding="utf-8").strip() if path.exists() else None
            if old != line:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(line + "\n", encoding="utf-8")
                report["key published" if old is None else "key replaced"] += 1
    return report


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="post season scores and rewrite the boards")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--results", required=True, type=Path)
    ap.add_argument("--root", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)
    data = json.loads(args.results.read_text(encoding="utf-8"))
    report = run(GitHub(args.repo), args.root.resolve(), data)
    print("Season entries:", ", ".join(f"{n} {k}" for k, n in sorted(report.items())) or "nothing to post")
    if report.get("key replaced"):
        print("::warning::SEASONS_SEED changed, so the season key changed: cards sealed with the old key won't "
              "open. Members make new cards with the pack, which fetches the new key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
