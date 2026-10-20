"""The seasons workflow's post job: checks the score job's results against GitHub, then comments on, labels and
closes each entry and rewrites the live boards.

    python -m seasons.engine.post --repo OWNER/NAME --results results.json

This job can write to the repo and its issues, but it never sees SEASONS_SEED or an entry's circuits. It uses a
result only if every field is in range and the issue still has the author, account and body the score job saw,
and everything it posts is a score, a rank or a fixed message: never anything a member wrote.

Season 3 also has its cases, which this job writes to seasons/cases once riggers.py has read each one back and it's
the account's own, and its locks: each account's first scored set of calls, kept in the state for good.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import kinds, seal
from .config import COMMENT_TAG, KEY_FILE, LOGIN, SEASONS, Season, season_of
from .gh import GitHub, GitHubError
from .score import body_sha, grader_sha, load_state, withdrawn

BOT = "github-actions[bot]"     # who the workflow's own token posts as
MAX_RESULTS = 5_000
WRITE_PAUSE = 1.0               # seconds after each write, well under GitHub's limit on new comments
LIST_ABOVE = 20                 # with more results than this, read the season's issues in pages, not one by one
TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
SHA = re.compile(r"[0-9a-f]{64}")
PROBLEMS = {"no-author", "no-card", "seal", "wrong-season", "not-yours", "bad-entry", "timeout", "crashed"}
SEAL_TEXTS = {   # SealError messages that open_card can raise, the only ones repeated to a member
    "no sealed card found: paste the whole block, BEGIN and END lines included",
    "the sealed card is damaged: copy it again from your terminal",
    "the sealed card doesn't open: copy it again from your terminal, and seal it with the current pack",
    "the sealed card is too big to score",
    "this card can't be opened",
}
SEAL_SEASON = re.compile(r"this card is for Season \d{1,3}, not Season \d{1,3}")
GONE = re.compile(r"HTTP 4(04|10)$")      # an issue that's been deleted


# ---- checking the score job's results ----

def _int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def check(item, contexts: dict) -> dict | None:
    """The result with only the fields this job uses, or None if anything is missing, odd or out of range.
    contexts: {season number: what its kind needs to check scores, such as Season 1's holes}."""
    if not isinstance(item, dict) or not _int(item.get("season"), 1, 255) or item["season"] not in SEASONS:
        return None
    if not (_int(item.get("issue"), 1, 10 ** 9) and isinstance(item.get("author"), str)
            and LOGIN.fullmatch(item["author"]) and _int(item.get("author_id"), 1, 10 ** 12)
            and isinstance(item.get("body_sha"), str) and SHA.fullmatch(item["body_sha"])
            and isinstance(item.get("time"), str) and TIME.fullmatch(item["time"])):
        return None
    out = {k: item[k] for k in ("season", "issue", "author", "author_id", "body_sha", "time")}
    kind = kinds.of(SEASONS[item["season"]])
    outcome = item.get("outcome")
    if outcome in ("waiting", "withdrawn"):
        return {**out, "outcome": outcome}
    if outcome == "problem":
        code = item.get("problem")
        if code not in PROBLEMS and code not in kind.PROBLEMS:
            return None
        message = item.get("message") if code == "seal" else None
        if message is not None and not (message in SEAL_TEXTS or SEAL_SEASON.fullmatch(str(message))):
            message = None
        return {**out, "outcome": "problem", "problem": code, **({"message": message} if message else {}),
                **kind.extra(item)}
    if outcome in getattr(kind, "OUTCOMES", {}):         # Season 3's case-open and locked
        fields = kind.check_outcome(item, contexts.get(item["season"]))
        return None if fields is None else {**out, "outcome": outcome, **fields}
    if outcome != "scored":
        return None
    scores = kind.check(item, contexts.get(item["season"]))
    return None if scores is None else {**out, "outcome": "scored", **scores}


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
    own = kinds.of(season).problem_text(season, result)
    if own:
        return own
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
        "bad-entry": f"This card doesn't match Season {n}. Make a new card with the latest Season {n} pack.",
        "timeout": f"Scoring this card took too long. Check it with `python {season.tool}`, then make a new card.",
        "crashed": f"Something in this card stopped the scorer. Check it with `python {season.tool}`, then make a "
                   f"new card.",
    }[code]


def comment_text(season: Season, result: dict, repo: str, standing=None) -> str:
    """What the workflow says on an entry. standing: the season's table after this run (kinds.of(season).table)."""
    n, number = season.number, result["issue"]
    outcome = result["outcome"]
    own = getattr(kinds.of(season), "outcome_lines", None)      # a kind's own words, such as Season 3's waiting line
    lines = own(season, result, repo, standing) if own else None
    if lines is not None:
        pass
    elif outcome == "waiting":
        lines = [f"{season.emoji} **Season {n}: your card is in.** Scoring switches on as soon as the club's season "
                 f"key is set up, and this comment will show your score then. There's nothing to do meanwhile."]
    elif outcome == "withdrawn":
        lines = [f"{season.emoji} **Season {n}: this entry is off the board** because the issue was closed as not "
                 f"planned. Reopen it to put it back."]
    elif outcome == "problem":
        again = ("replace the card in this entry with the new one" if season.on_site
                 else "edit this issue and paste the new card in")
        lines = [f"{season.emoji} **Season {n}: this entry isn't scored yet.** {problem_text(season, result)}", "",
                 f"Then {again}, or send it {season.send(season.form_url(repo))}. It's scored again within minutes."]
    else:
        lines = kinds.of(season).scored_lines(season, result, repo, standing)
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


SAME = ("body_sha", "outcome", "problem", "message", "time")    # and each kind's FIELDS, EXTRA and OUTCOMES' fields


def agrees(state: dict, r: dict, kind) -> tuple:
    """Season 3: (whether a result agrees with its account's lock, whether it made the lock). Each account's first
    scored set of calls is its only one: once it's locked, only that entry can be scored (with the lock's values),
    a later entry is "locked" to it, and either can be withdrawn. Without a lock, nothing can be "locked" yet."""
    locks = state.setdefault("locks", {})
    account, outcome = str(r["author_id"]), r["outcome"]
    lock = locks.get(account)
    if not isinstance(lock, dict):
        if outcome == "locked":
            return False, False
        if outcome == "scored":
            locks[account] = {"issue": r["issue"], "author": r["author"], "time": r["time"],
                              **{k: r[k] for k in kind.FIELDS}}
            return True, True
        return True, False
    if outcome == "scored":
        return lock.get("issue") == r["issue"] and all(lock.get(k) == r[k] for k in ("time", *kind.FIELDS)), False
    if outcome == "locked":
        return lock.get("issue") == r["scored_issue"], False
    return outcome == "withdrawn", False


def write_case(root: Path, season: Season, r: dict) -> bool:
    """Writes a builder's case where the pack downloads it from, unless it's there already. True if it wrote."""
    path = season.case_file(root, r["author"])
    if path.is_file() and path.read_text(encoding="utf-8") == r["case_text"]:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r["case_text"], encoding="utf-8")
    return True


def close_cases(gh, season: Season, state: dict, live: dict, report: Counter) -> bool:
    """Season 3: once an account's calls are scored, each of its other open cases says so and is closed, and is
    locked to the scored one from then on. It looks every run, so a close that failed is tried again (unless the
    issue's been deleted). True if it changed the state."""
    kind, locks, done = kinds.of(season), state.get("locks") or {}, False
    for key in sorted(state["issues"], key=int):
        entry = state["issues"][key]
        lock = locks.get(str(entry.get("author_id")))
        if (entry.get("outcome") != "case-open" or not isinstance(lock, dict) or not isinstance(lock.get("issue"), int)
                or lock["issue"] == int(key)):
            continue
        number = int(key)
        try:
            issue = live.get(number) or gh.issue(number)
            if not issue or (issue.get("user") or {}).get("id") != entry.get("author_id") or withdrawn(issue):
                continue          # not the same entry any more, or withdrawn: the score job sees to it
            text = "\n".join(kind.closed_lines(season, lock["issue"]) + ["", tag(season, number)])
            entry["comment_id"] = say(gh, number, season, text, entry.get("comment_id"))
            if issue.get("state") == "open":
                gh.set_state(number, "closed", "completed")
                gh.pause(WRITE_PAUSE)
            report["case closed"] += 1
        except GitHubError as error:
            if not GONE.search(str(error)):
                print(f"Couldn't close #{number} ({error}): the next run tries again.", file=sys.stderr)
                report["failed"] += 1
                continue
        state["issues"][key] = {**{k: v for k, v in entry.items() if k != "fingerprint"}, "outcome": "locked",
                                "scored_issue": lock["issue"]}
        done = True
    return done


def run(gh, root: Path, data, now: datetime | None = None) -> Counter:
    now = now or datetime.now(timezone.utc)
    if not isinstance(data, dict) or data.get("v") != 1 or data.get("repo") != gh.repo:
        raise SystemExit("results.json isn't from this repo's score job")
    raw = data.get("results")
    if not isinstance(raw, list) or len(raw) > MAX_RESULTS:
        raise SystemExit("results.json has no list of results, or far too many")
    report = Counter()
    contexts = {n: kinds.of(s).context(root, s) for n, s in SEASONS.items()}
    states = {n: load_state(root, s) for n, s in SEASONS.items()}
    changed = set()

    for number, sha in (data.get("full") or {}).items():         # every entry was scored again for this grader
        if str(number).isdigit() and int(number) in SEASONS and sha == grader_sha(root, SEASONS[int(number)]):
            states[int(number)]["grader"] = sha
            changed.add(int(number))

    results, seen = [], set()
    for item in raw:
        r = check(item, contexts)
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
        kind = kinds.of(season)
        state = states[season.number]
        known = state["issues"].get(key)
        if getattr(kind, "ONE_SET", False):
            ok, locked = agrees(state, r, kind)
            if not ok:
                report["dropped"] += 1
                continue
            if locked:
                changed.add(season.number)
        if r["outcome"] == "case-open" and write_case(root, season, r):
            report["case written"] += 1
        if r["outcome"] == "waiting":
            if key in state["waiting"] and not known:
                continue                                  # already told it's waiting
            state["issues"].pop(key, None)
            todo.append((season, r, issue, known))
            changed.add(season.number)
            continue
        kept = {k for fields in getattr(kind, "OUTCOMES", {}).values() for k in fields}     # never a case's text
        entry = {"author": r["author"], "author_id": r["author_id"], "body_sha": r["body_sha"], "time": r["time"],
                 "outcome": r["outcome"]}
        entry.update({k: r[k] for k in ("problem", "message", *kind.FIELDS, *kind.EXTRA, *sorted(kept)) if k in r})
        comment_id = (known or {}).get("comment_id") or state["waiting"].get(key)
        if comment_id:
            entry["comment_id"] = comment_id
        if known and all(known.get(k) == entry.get(k) for k in (*SAME, *kind.FIELDS, *kind.EXTRA, *sorted(kept))):
            continue                                      # scored again, same result: nothing to say
        state["issues"][key] = entry
        state["waiting"].pop(key, None)
        todo.append((season, r, issue, known))
        changed.add(season.number)

    tables = {n: kinds.of(SEASONS[n]).table(states[n]) for n in changed}
    labelled = set()
    for season, r, issue, known in todo:
        state, key = states[season.number], str(r["issue"])
        try:
            if season.number not in labelled:
                gh.ensure_label(season.label, season.colour, f"An entry for {season.title}")
                labelled.add(season.number)
            text = comment_text(season, r, gh.repo, tables[season.number])
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
            if r["outcome"] in ("scored", "locked") and issue.get("state") == "open":
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

    for number, season in SEASONS.items():
        if getattr(kinds.of(season), "ONE_SET", False) and close_cases(gh, season, states[number], live, report):
            changed.add(number)

    updated = now.strftime("%Y-%m-%d %H:%M UTC")
    for number, season in SEASONS.items():
        md, js = season.board(root, "md"), season.board(root, "json")
        if number in changed or not md.exists() or not js.exists():
            text, data_text = kinds.of(season).board(season, states[number], contexts[number], updated,
                                                     season.form_url(gh.repo))
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
