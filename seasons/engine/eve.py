"""Season 4's kind of season, Catch Eve (BB84 from the spy's side): a badge season, with no podium on purpose.

Quantum mechanics has its own ceiling, 81.29% of the key at 11% errors for any attack on one qubit at a time, and
several attacks reach it, so a ranked board would only be a race to copy one. Instead there are three bars, and the
live board lists everyone past each one, in the order they got there. A bar stays yours once any of your entries
reaches it, until that entry comes off the board or its card is replaced. The board also shows the smallest Ghost,
in strokes.

The thresholds are the referee's own (projects/flagships/season-04-catch-eve/referee.py), and a test keeps them
equal.
"""

from __future__ import annotations

import json
import math

from .boards import names, plural
from .config import Season

FIELDS = ("errors", "steal", "spies", "strokes")   # the exact rates, rounded to 9 decimal places, and the spy's size
EXTRA = ("part",)                                  # which circuit a "bad-circuit" problem is about
WORDS = {"work": "spy", "public": "steal, errors, spy count and strokes", "back": "steal, errors and bars",
         "again": "Every bar you reach stays yours: send a new card whenever you like."}
LINE = 0.11                                   # more errors than this in the kept bits, and the key is thrown away
PROJECT_07 = 0.5 + 0.44 / 4                   # Project 07's eavesdropper on 44% of the qubits: 61%
CEILING = 0.5 + math.sqrt(LINE * (1 - LINE))  # the most any attack on one qubit at a time steals at 11% errors
CEILING_SLACK = 1e-6
ROUNDING = 5e-10                              # the most a rate rounded to 9 decimal places is from the exact one
MAX_SPIES = 2
MAX_STROKES = 3 * 500 * 10                    # three circuits of at most 500 gates, each at most 10 strokes
PARTS = ("attack", "listen Z", "listen X")
NAMES = {"attack": "your attack", "listen Z": "your listening in the Z basis",
         "listen X": "your listening in the X basis"}
# (key, emoji, one builder's title, the list's title, what it takes)
BARS = [("eavesdropper", "👂", "Eavesdropper", "Eavesdroppers",
         "past 61% without getting caught, more than Project 07's eavesdropper steals"),
        ("ghost", "👻", "Ghost", "Ghosts",
         "81.29% without getting caught, the most any attack on one qubit at a time can steal"),
        ("wolf", "🐺", "Lone Wolf", "Lone Wolves",
         "81.29% without getting caught, with a single spy qubit")]
PROBLEMS = {
    "no-spy": "This card has no spy in it. Make a new card with `{command}` in the Season {n} pack.",
    "bad-parts": "This card doesn't hold Season {n}'s three circuits. Make a new card with the latest Season {n} pack.",
    "bad-circuit": "{part} in this card can't be scored. Run `python referee.py` in the Season {n} pack to see "
                   "why, then make a new card.",
    "bad-entry": "This card doesn't match Season {n}. Make a new card with the latest Season {n} pack.",
    "timeout": "Scoring this card took too long. Check it with `python referee.py`, then make a new card.",
    "crashed": "Something in this card stopped the referee. Check it with `python referee.py`, then make a new card.",
}


def rate(p: float) -> float:
    return round(float(p), 9)


def caught(errors: float) -> bool:
    return rate(errors) > LINE


def bars(errors: float, steal: float, spies: int) -> dict:
    """{bar key: cleared?}, exactly as the referee works it out from the same rounded rates and spy count."""
    safe = not caught(errors)
    ceiling = safe and rate(steal) >= rate(CEILING - CEILING_SLACK)
    return {"eavesdropper": safe and rate(steal) > rate(PROJECT_07), "ghost": ceiling, "wolf": ceiling and spies == 1}


def percent(p: float) -> str:
    """Two decimal places, or more when that's what tells a hair past a line from right on it."""
    text = f"{100 * p:.2f}%"
    for line in (LINE, PROJECT_07):
        if text == f"{100 * line:.2f}%" and rate(p) != rate(line):
            text = f"{100 * p:.7f}".rstrip("0") + "%"
    return text


def context(root, season: Season) -> None:
    return None


def _rate_ok(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and 0 <= value <= 1 and rate(value) == value)


def _whole(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def check(item: dict, ctx=None) -> dict | None:
    """A scored result's rates and spy, if they're real rounded rates no attack can beat, else None."""
    errors, steal, spies, strokes = (item.get(k) for k in FIELDS)
    if not (_rate_ok(errors) and _rate_ok(steal) and _whole(spies, 1, MAX_SPIES) and _whole(strokes, 0, MAX_STROKES)):
        return None
    # Past what quantum mechanics allows. The referee checks 1/2 + √(D(1 - D)) on the exact rates, then rounds them,
    # and near 0% or 100% errors a rounding moves that bound far more than it moves D: so take the exact D this
    # rounded one could have come from that allows the most.
    near = max(abs(errors - 0.5) - ROUNDING, 0.0)
    if steal > 0.5 + math.sqrt(0.25 - near * near) + CEILING_SLACK + ROUNDING:
        return None
    return {"errors": float(errors), "steal": float(steal), "spies": spies, "strokes": strokes}


def extra(item: dict) -> dict:
    part = item.get("part")
    return {"part": part} if item.get("problem") == "bad-circuit" and part in PARTS else {}


def problem_text(season: Season, result: dict) -> str | None:
    text = PROBLEMS.get(result.get("problem"))
    if not text:
        return None
    part = NAMES.get(result.get("part"), "one of the circuits")
    text = text.format(n=season.number, command=season.command.format(login=result["author"]), part=part)
    return text[0].upper() + text[1:]


def _cleared(entry: dict) -> dict:
    return bars(entry["errors"], entry["steal"], entry["spies"])


def table(state: dict) -> dict:
    """{"builders": how many have a scored entry, "bars": {key: [the entry that first got each builder past it,
    in the order they got there]}}."""
    people = set()
    firsts = {key: {} for key, *_ in BARS}
    for number, entry in state.get("issues", {}).items():
        if entry.get("outcome") != "scored":
            continue
        who = entry.get("author_id") or entry["author"].casefold()
        people.add(who)
        mine = {**entry, "issue": int(number), "who": who}
        for key, cleared in _cleared(entry).items():
            held = firsts[key].get(who)
            if cleared and (held is None or (mine["time"], mine["issue"]) < (held["time"], held["issue"])):
                firsts[key][who] = mine
    lists = {key: sorted(found.values(), key=lambda e: (e["time"], e["issue"])) for key, found in firsts.items()}
    return {"builders": len(people), "bars": lists}


def _me(entry: dict, author_id: int, author: str) -> bool:
    if author_id and entry.get("author_id"):
        return entry["author_id"] == author_id
    return entry["author"].casefold() == author.casefold()


def _spy(result: dict) -> str:
    spies = "1 spy qubit" if result["spies"] == 1 else f"{result['spies']} spy qubits"
    return f"{spies}, {plural(result['strokes'], 'stroke')}"


def scored_lines(season: Season, result: dict, repo: str, standing: dict) -> list:
    n = season.number
    board = f"[live board]({season.board_link(repo)})"
    errors, steal = result["errors"], result["steal"]
    if caught(errors):
        lines = [f"{season.emoji} **Season {n}, scored:** caught. Your spy causes {percent(errors)} errors, over the "
                 f"11% line, so Alice and Bob throw the key away: the {percent(steal)} it would have stolen counts "
                 f"for nothing. Your spy: {_spy(result)}.", ""]
    else:
        lines = [f"{season.emoji} **Season {n}, scored:** steal {percent(steal)} at {percent(errors)} errors, at or "
                 f"under the 11% line, so Alice and Bob keep the key. Your spy: {_spy(result)}.", ""]
    cleared = _cleared(result)
    got = [f"{emoji} {one}" for key, emoji, one, _, _ in BARS if cleared[key]]
    lines.append(f"This card clears {_and(got)}." if got else "This card doesn't clear a bar yet.")
    places, to_go = [], []
    for key, emoji, one, _, rule in BARS:
        entries = (standing or {}).get("bars", {}).get(key, [])
        place = next((i for i, e in enumerate(entries, 1) if _me(e, result["author_id"], result["author"])), None)
        if place:
            places.append(f"{emoji} {one} #{place}")
        else:
            to_go.append(f"{emoji} {one}, {rule}")
    if places:
        lines.append(f"You're on the {board}: {_and(places)}.")
    if to_go:
        command = season.command.format(login=result["author"])
        lines += ["", "Still to reach: " + "; ".join(to_go) + ".", "",
                  f"Go for {'it' if len(to_go) == 1 else 'them'} whenever you like: change spy.py, run `{command}` "
                  f"again and send the new card {season.send(season.form_url(repo))}."]
    else:
        lines += ["", "That's every bar this season."]
    lines += ["", "Your circuits stay sealed: only your steal, errors, spy count and strokes are public."]
    return lines


def _and(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def board(season: Season, state: dict, ctx, updated: str, form_url: str) -> tuple:
    """The live board as (markdown, json text)."""
    standing = table(state)
    lists = standing["bars"]
    ghosts = [e for e in state.get("issues", {}).values() if e.get("outcome") == "scored" and _cleared(e)["ghost"]]
    smallest = min((e["strokes"] for e in ghosts), default=None)
    at_smallest = {e.get("author_id") or e["author"].casefold() for e in ghosts if e["strokes"] == smallest}
    data = {"season": season.number, "title": season.title, "updated": updated, "builders": standing["builders"]}
    data.update({many.lower().replace(" ", "_"): [e["author"] for e in lists[key]] for key, _, _, many, _ in BARS})
    data["smallest_ghost"] = smallest
    data["smallest_ghost_builders"] = len(at_smallest)
    lines = [f"# {season.title}", "",
             f"Live board · entries from {plural(standing['builders'], 'builder')} · updated {updated}", "",
             "No podium this season, on purpose: quantum mechanics has a ceiling, and a spy that reaches it can only "
             "be matched, never beaten. So the board lists everyone past each bar, in the order they got there.", ""]
    for key, emoji, _, many, rule in BARS:
        lines.append(f"{emoji} **{many}** ({rule}): {names(lists[key])}")
    reached = (f"{plural(smallest, 'stroke')}, reached by {plural(len(at_smallest), 'builder')}"
               if smallest is not None else "nobody yet")
    lines += ["", f"**Smallest Ghost:** {reached} (strokes only: every spy stays sealed)", "",
              f"To get on it: seal your card with `{season.command.format(login='YOUR_GITHUB_USERNAME')}` in the "
              f"Season {season.number} pack, then send it {season.send(form_url)}. Your bars come back on your "
              f"entry within minutes.", ""]
    return "\n".join(lines), json.dumps(data, indent=1, ensure_ascii=False) + "\n"
