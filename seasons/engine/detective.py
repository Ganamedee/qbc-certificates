"""Season 3's kind of season, Rigged RNG Hunt: a badge season of personal cases, with no podium on purpose.

Every GitHub account gets a case of its own, twelve files of bits of which three to five are rigged, and one set of
calls, scored once: the first set scored locks the account for good. The season never closes, so a case shared by
everyone would leak its answers to anyone with spare accounts. With a case each, there's nothing to share but
methods, and no race to win. The board lists everyone past each bar, the spread of scores and how often each flaw
is caught: counts only, never a file number or a case's layout.

The flaws and the case files are riggers.py's (projects/flagships/season-03-rng-hunt). The post job loads it out of
the grader folder to check every case it publishes. FLAWS and NAMES are its own, and a test keeps them equal.
"""

from __future__ import annotations

import json
import re
import types
from collections import Counter
from pathlib import Path

from .boards import names, plural
from .config import SITE, Season

FIELDS = ("caught", "false_alarms", "rigged", "flaws")      # what a scored set of calls carries
EXTRA = ()                                                   # nothing more on a problem
OUTCOMES = {"case-open": ("fingerprint",), "locked": ("scored_issue",)}   # more outcomes, and what state keeps
ONE_SET = True                     # one scored set of calls per GitHub account, kept in the state's locks
WORDS = {"work": "calls", "public": "score", "back": "score",
         "again": "One set of calls per GitHub account: once it's scored, your case is closed for good."}
FLAWS = ("loaded", "sticky", "warming", "lazy", "loop")
NAMES = {"loaded": "🪙 the loaded coin", "sticky": "🧲 the sticky coin", "warming": "🌡️ the warming coin",
         "lazy": "🧱 the lazy bit", "loop": "🔁 the short loop"}
SHARP = 3                          # every case has at least three rigged files
# (key, emoji, name, what it takes)
BARS = [("sharp", "🔍", "Sharp Eye", f"a score of {SHARP} or more"),
        ("sweep", "🧹", "Clean Sweep", "every rigged file in the case caught, with no false alarms")]
MAX_CASE = 60_000                  # characters in a published case (one is about 48,200)
SHA = re.compile(r"[0-9a-f]{64}")
PROBLEMS = {   # this season's own problem codes, and its wording for the shared ones
    "wrong-case": "These calls were made on a different case from yours. Download your own case with "
                  "`python detective.py --case {login}`, make your calls on it, then make a new card with "
                  "`{command}`.",
    "bad-calls": "The calls in this card can't be read: each file from 01 to 12 can be called once. Make a new card "
                 "with `{command}` in the latest Season {n} pack, which checks calls.txt before it seals.",
    "bad-entry": "This card doesn't match Season {n}. Make a new card with `{command}` in the latest Season {n} pack.",
    "timeout": "Scoring this card took too long. Make a new card with `{command}`.",
    "crashed": "Something in this card stopped the scorer. Make a new card with `{command}` in the latest Season {n} "
               "pack.",
}


# ---- riggers.py, and checking the score job's results ----

def load(path: Path):
    """riggers.py as a module, compiled from its source, so nothing is written next to it."""
    path = Path(path)
    module = types.ModuleType("riggers")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def context(root, season: Season):
    """The season's riggers.py from its grader folder, or None if it isn't there (then nothing is posted)."""
    path = Path(root) / season.grader / "riggers.py"
    return load(path) if path.is_file() else None


def _int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def check(item: dict, riggers) -> dict | None:
    """A scored result's counts, if they add up for a case riggers.py could make, else None."""
    if riggers is None:
        return None
    rigged, caught, alarms, flaws = item.get("rigged"), item.get("caught"), item.get("false_alarms"), item.get("flaws")
    if not (_int(rigged, riggers.LOW, riggers.HIGH) and _int(caught, 0, rigged)
            and _int(alarms, 0, riggers.FILES - rigged)):
        return None
    if not (isinstance(flaws, dict) and len(flaws) == rigged and set(flaws) <= set(riggers.FLAWS)
            and all(v in ("caught", "missed") for v in flaws.values())
            and sum(v == "caught" for v in flaws.values()) == caught):
        return None
    return {"caught": caught, "false_alarms": alarms, "rigged": rigged,
            "flaws": {f: flaws[f] for f in riggers.FLAWS if f in flaws}}


def check_outcome(item: dict, riggers) -> dict | None:
    """The fields of a "case-open" or "locked" result, if they hold up, else None. A case is only published if
    riggers.py reads it, it's this account's (by username and account id) and it's written exactly as riggers.py
    writes it."""
    outcome = item.get("outcome")
    if outcome == "locked":
        scored = item.get("scored_issue")
        return {"scored_issue": scored} if _int(scored, 1, 10 ** 9) and scored != item.get("issue") else None
    if outcome != "case-open" or riggers is None:
        return None
    fingerprint, text = item.get("fingerprint"), item.get("case_text")
    if not (isinstance(fingerprint, str) and SHA.fullmatch(fingerprint) and isinstance(text, str)
            and len(text) <= MAX_CASE):
        return None
    try:
        case = riggers.read_case_text(text)
    except ValueError:                 # riggers.CaseError, or a number it can't read
        return None
    if not (case["github"].casefold() == item["author"].casefold() and case["account"] == item["author_id"]
            and case["fingerprint"] == fingerprint
            and riggers.case_text(case["files"], case["github"], case["account"]) == text):
        return None
    return {"fingerprint": fingerprint, "case_text": text}


def extra(item: dict) -> dict:
    return {}


# ---- the bars, and what the workflow says ----

def score_of(entry: dict) -> int:
    return entry["caught"] - entry["false_alarms"]


def bars(entry: dict) -> dict:
    """{bar key: cleared?} for a scored set of calls."""
    return {"sharp": score_of(entry) >= SHARP,
            "sweep": entry["caught"] == entry["rigged"] and entry["false_alarms"] == 0}


def table(state: dict) -> dict:
    """{"cases": each builder's scored entry, in the order they were scored, "bars": {key: the ones past each bar,
    in that order}, "locks": {account id: its lock}}. Only an account's first scored set of calls counts."""
    locks = state.get("locks") or {}
    found = {}
    for number, entry in state.get("issues", {}).items():
        if entry.get("outcome") != "scored":
            continue
        lock = locks.get(str(entry.get("author_id")))
        if lock and lock.get("issue") != int(number):
            continue
        who = entry.get("author_id") or entry["author"].casefold()
        mine = {**entry, "issue": int(number)}
        held = found.get(who)
        if held is None or (mine["time"], mine["issue"]) < (held["time"], held["issue"]):
            found[who] = mine
    cases = sorted(found.values(), key=lambda e: (e["time"], e["issue"]))
    return {"cases": cases, "bars": {key: [e for e in cases if bars(e)[key]] for key, *_ in BARS}, "locks": locks}


def problem_text(season: Season, result: dict) -> str | None:
    text = PROBLEMS.get(result.get("problem"))
    if not text:
        return None
    return text.format(n=season.number, login=result["author"],
                       command=season.command.format(login=result["author"]))


def scorecard(season: Season, result: dict) -> str:
    return (f"{season.emoji} {season.name} S{season.number} · score {score_of(result)} · caught {result['caught']} "
            f"of {result['rigged']} · {plural(result['false_alarms'], 'false alarm')}")


def _and(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _me(entry: dict, result: dict) -> bool:
    if result["author_id"] and entry.get("author_id"):
        return entry["author_id"] == result["author_id"]
    return entry["author"].casefold() == result["author"].casefold()


def scored_lines(season: Season, result: dict, repo: str, standing: dict) -> list:
    n = season.number
    board = f"[live board]({season.board_link(repo)})"
    caught, alarms, rigged = result["caught"], result["false_alarms"], result["rigged"]
    lines = [f"{season.emoji} **Season {n}, case closed: score {score_of(result)}.** You caught {caught} of the "
             f"{rigged} rigged files in your case, with {plural(alarms, 'false alarm')}.", ""]
    got = [NAMES[f] for f in FLAWS if result["flaws"].get(f) == "caught"]
    missed = [NAMES[f] for f in FLAWS if result["flaws"].get(f) == "missed"]
    lines.append(f"Caught: {_and(got) if got else 'none'}. Missed: {_and(missed)}." if missed
                 else "You caught every one.")
    cleared = bars(result)
    clears = [f"{emoji} {name}" for key, emoji, name, _ in BARS if cleared[key]]
    lines += ["", f"This clears {_and(clears)}." if clears else "This doesn't clear a bar."]
    places = []
    for key, emoji, name, _ in BARS:
        entries = (standing or {}).get("bars", {}).get(key, [])
        place = next((i for i, e in enumerate(entries, 1) if _me(e, result)), None)
        if place:
            places.append(f"{emoji} {name} #{place}")
    lines.append(f"You're on the {board}: {_and(places)}." if places
                 else f"Your score counts in the spread on the {board}.")
    lines += ["", f"Your scorecard line: `{scorecard(season, result)}`", "",
              "Your case is closed for good: one set of calls per builder, so nobody can work the answers out by "
              "trying again. Your calls stay sealed and only your score is public. Which file held which flaw stays "
              "secret."]
    return lines


def outcome_lines(season: Season, result: dict, repo: str, standing: dict) -> list | None:
    """This season's own words for an entry that isn't scored or a problem: waiting, case-open and locked."""
    n, login, outcome = season.number, result["author"], result["outcome"]
    if outcome == "waiting":
        return [f"{season.emoji} **Season {n}: you're in.** Cases open and calls are scored as soon as the club's "
                f"season key is set up, and this comment will update then. There's nothing to do meanwhile."]
    if outcome == "case-open":
        send = (f"send the sealed card into this entry: press **Send your calls** on [its page on the club site]"
                f"({season.entry_page(result['issue'])})"
                if season.on_site else "edit this issue and paste the sealed card into the calls box")
        return [f"{season.emoji} **Season {n}: your case is open.** Twelve files of 24,000 bits, made for your GitHub "
                f"account alone. Between three and five of them are rigged.", "",
                f"Download it in the Season {n} pack, inside your venv: `python detective.py --case {login}`. "
                f"GitHub can take a few minutes to serve a new file, so if it isn't there yet, try again in a few "
                f"minutes. If the download can't reach GitHub, save [your case file]"
                f"({season.case_page_url(repo, login)}) from your browser and run "
                f"`python detective.py --case-file FILE` instead.", "",
                f"Case fingerprint: `{result['fingerprint'][:12]}`", "",
                f"When your calls are ready, run `{season.command.format(login=login)}`, then {send}. You get one "
                f"set of calls, so practise first with `python detective.py --practice`."]
    if outcome == "locked":
        lock = ((standing or {}).get("locks") or {}).get(str(result["author_id"])) or {}
        score = f" (score {score_of(lock)})" if all(k in lock for k in FIELDS) else ""
        return [f"{season.emoji} **Season {n}: your case is already closed**, with entry #{result['scored_issue']}"
                f"{score}. One set of calls per builder, so this entry isn't scored, and your score stands."]
    return None


def closed_lines(season: Season, scored_issue: int) -> list:
    """What an account's other open cases say once its calls are scored."""
    return [f"{season.emoji} **Season {season.number}.** Your calls were scored in #{scored_issue}, so this case is "
            f"closed. Your score and the bars it clears are on that entry."]


def form(season: Season, repo: str) -> dict:
    """The entry form's words: the box is left empty to open a case, then holds the sealed calls."""
    n, command = season.number, season.command.format(login="YOUR_GITHUB_USERNAME")
    intro = (f"**To open your case**, leave the box below empty and send it. Within minutes the workflow comments "
             f"here with your case, twelve files of bits made for your GitHub account alone, and the command that "
             f"downloads it into the Season {n} pack.\n\n"
             f"**When your calls are ready**, run `{command}` in the pack, inside your venv. Then edit this issue "
             f"and paste the whole sealed block in, BEGIN and END lines included. Your score comes back as a comment "
             f"within minutes and goes on the [live board]({season.board_url(repo)}). Your calls stay sealed: only "
             f"the club's scoring workflow can open them.\n\n"
             f"One case per GitHub account, and one set of calls: once they're scored, your case is closed for good. "
             f"Practise first with `python detective.py --practice`.")
    return {"description": f"Open your Season {n} case, then send your sealed calls: your score comes back within "
                           f"minutes.",
            "intro": intro,
            "hint": f"Leave it empty to open your case. Later, paste the whole block that `{command}` prints.",
            "required": False}


def board(season: Season, state: dict, riggers, updated: str, form_url: str) -> tuple:
    """The live board as (markdown, json text)."""
    standing = table(state)
    cases, lists = standing["cases"], standing["bars"]
    spread = sorted(Counter(score_of(e) for e in cases).items(), reverse=True)
    flaws = [{"flaw": f, "name": NAMES[f], "caught": sum(e["flaws"].get(f) == "caught" for e in cases),
              "cases": sum(f in e["flaws"] for e in cases)} for f in FLAWS]
    data = {"season": season.number, "title": season.title, "updated": updated, "cases_closed": len(cases),
            "sharp_eye": [e["author"] for e in lists["sharp"]], "clean_sweep": [e["author"] for e in lists["sweep"]],
            "scores": [{"score": s, "builders": k} for s, k in spread], "flaws": flaws}
    lines = [f"# {season.title}", "",
             f"Live board · {plural(len(cases), 'case')} closed · updated {updated}", "",
             "No podium this season, on purpose: every builder gets a case of their own, so there's no race to win. "
             "The board lists everyone past each bar, in the order they got there.", ""]
    for key, emoji, name, rule in BARS:
        lines.append(f"{emoji} **{name}** ({rule}): {names(lists[key])}")
    so_far = " · ".join(f"{s}: {plural(k, 'builder')}" for s, k in spread) or "nobody yet"
    lines += ["", f"**Scores so far:** {so_far}", "",
              "**The flaws, and how often they're caught** (only counts: every case stays secret)", ""]
    for f in flaws:
        lines.append(f"- {f['name']}: " + (f"caught in {f['caught']} of {plural(f['cases'], 'case')} that had one"
                                           if f["cases"] else "no closed case has had one yet"))
    n, command = season.number, season.command.format(login="YOUR_GITHUB_USERNAME")
    if season.on_site:
        how = (f"To get on it: press **Open my case** on the [club's season page]({SITE}/season#case-{n}). When your "
               f"calls are ready, seal them with `{command}` in the Season {n} pack and press **Send your calls** on "
               f"your entry's page. Your score comes back on your entry within minutes.")
    else:
        how = (f"To get on it: open your case with the [Season {n} entry form]({form_url}), calls box empty. When "
               f"your calls are ready, seal them with `{command}` in the Season {n} pack and paste the card into the "
               f"same entry. Your score comes back on your entry within minutes.")
    lines += ["", how, ""]
    return "\n".join(lines), json.dumps(data, indent=1, ensure_ascii=False) + "\n"
