"""Season 2's kind of season, Beat Einstein (the CHSH game): a badge season, with no podium on purpose.

Quantum mechanics has its own ceiling, 85.36%, and a textbook strategy reaches it, so a ranked board would only be
a race to copy it. Instead there are three bars, and the live board lists everyone past each one, in the order they
got there. A bar stays yours once any of your entries reaches it (until you withdraw that entry).

The thresholds are the referee's own (projects/flagships/season-02-chsh/referee.py), and a test keeps them equal.
"""

from __future__ import annotations

import json
import math

from .boards import names, plural
from .config import Season

FIELDS = ("perfect", "decaying")      # the exact win rates on each chip, rounded to 9 decimal places
EXTRA = ("part",)                     # which circuit a "bad-circuit" problem is about
WORDS = {"work": "strategy", "public": "win rates", "back": "win rates and bars",
         "again": "Every bar you reach stays yours: send a new card whenever you like, or edit this issue and paste "
                  "it in."}
LIMIT = 0.75                                  # the most any classical team wins, on average
CEILING = math.cos(math.pi / 8) ** 2          # Tsirelson's bound: the most any quantum strategy wins
CEILING_SLACK = 1e-6
PARTS = ("shared", "alice 0", "alice 1", "bob 0", "bob 1")
NAMES = {"shared": "the shared pair", "alice 0": "Alice's move for x = 0", "alice 1": "Alice's move for x = 1",
         "bob 0": "Bob's move for y = 0", "bob 1": "Bob's move for y = 1"}
# (key, emoji, one builder's title, the list's title, what it takes)
BARS = [("bell", "🔔", "Bell Breaker", "Bell Breakers",
         "past 75% on the perfect chip, the most any classical team can win"),
        ("ceiling", "🏔️", "Ceiling Club", "Ceiling Club",
         "85.36% on the perfect chip, the most quantum mechanics allows"),
        ("decay", "🍂", "Decay Breaker", "Decay Breakers",
         "past 75% on the decaying chip, where the textbook strategy falls short")]
PROBLEMS = {
    "no-strategy": "This card has no strategy in it. Make a new card with `{command}` in the Season {n} pack.",
    "bad-parts": "This card doesn't hold Season {n}'s five circuits. Make a new card with the latest Season {n} pack.",
    "bad-circuit": "{part} in this card can't be scored. Run `python referee.py` in the Season {n} pack to see "
                   "why, then make a new card.",
    "bad-entry": "This card doesn't match Season {n}. Make a new card with the latest Season {n} pack.",
    "timeout": "Scoring this card took too long. Check it with `python referee.py`, then make a new card.",
    "crashed": "Something in this card stopped the referee. Check it with `python referee.py`, then make a new card.",
}


def rate(win: float) -> float:
    return round(float(win), 9)


def bars(perfect: float, decaying: float) -> dict:
    """{bar key: cleared?}, exactly as the referee works it out from the same two rounded win rates."""
    return {"bell": rate(perfect) > LIMIT, "ceiling": rate(perfect) >= rate(CEILING - CEILING_SLACK),
            "decay": rate(decaying) > LIMIT}


def percent(p: float) -> str:
    """Two decimal places, or more when that's what tells a hair past 75% from exactly 75%."""
    text = f"{100 * p:.2f}%"
    if text == "75.00%" and rate(p) != LIMIT:
        text = f"{100 * p:.7f}".rstrip("0") + "%"
    return text


def context(root, season: Season) -> None:
    return None


def _rate_ok(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and 0 <= value <= CEILING + 1e-6 and rate(value) == value)


def check(item: dict, ctx=None) -> dict | None:
    """A scored result's win rates, if both are real rounded win rates no strategy can beat, else None."""
    perfect, decaying = item.get("perfect"), item.get("decaying")
    if not (_rate_ok(perfect) and _rate_ok(decaying)):
        return None
    return {"perfect": float(perfect), "decaying": float(decaying)}


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
        for key, cleared in bars(entry["perfect"], entry["decaying"]).items():
            held = firsts[key].get(who)
            if cleared and (held is None or (mine["time"], mine["issue"]) < (held["time"], held["issue"])):
                firsts[key][who] = mine
    lists = {key: sorted(found.values(), key=lambda e: (e["time"], e["issue"])) for key, found in firsts.items()}
    return {"builders": len(people), "bars": lists}


def _me(entry: dict, author_id: int, author: str) -> bool:
    if author_id and entry.get("author_id"):
        return entry["author_id"] == author_id
    return entry["author"].casefold() == author.casefold()


def scored_lines(season: Season, result: dict, repo: str, standing: dict) -> list:
    n = season.number
    form = f"[Season {n} entry form]({season.form_url(repo)})"
    board = f"[live board]({season.board_url(repo)})"
    p, d = result["perfect"], result["decaying"]
    lines = [f"{season.emoji} **Season {n}, scored:** perfect chip {percent(p)} (S = {8 * p - 4:.3f}), decaying chip "
             f"{percent(d)} (S = {8 * d - 4:.3f}).", ""]
    cleared = bars(p, d)
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
                  f"Go for {'it' if len(to_go) == 1 else 'them'} whenever you like: change strategy.py, run "
                  f"`{command}` again and send the new card with the {form}, or edit this issue and paste it in."]
    else:
        lines += ["", "That's every bar this season."]
    lines += ["", "Your circuits stay sealed: only your win rates are public."]
    return lines


def _and(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def board(season: Season, state: dict, ctx, updated: str, form_url: str) -> tuple:
    """The live board as (markdown, json text)."""
    standing = table(state)
    lists = standing["bars"]
    scored = [e for e in state.get("issues", {}).values() if e.get("outcome") == "scored"]
    best = max((e["decaying"] for e in scored), default=None)
    at_best = {e.get("author_id") or e["author"].casefold() for e in scored if e["decaying"] == best}
    data = {"season": season.number, "title": season.title, "updated": updated, "builders": standing["builders"]}
    data.update({many.lower().replace(" ", "_"): [e["author"] for e in lists[key]] for key, _, _, many, _ in BARS})
    data["best_decaying"] = best
    lines = [f"# {season.title}", "",
             f"Live board · entries from {plural(standing['builders'], 'builder')} · updated {updated}", "",
             "No podium this season, on purpose: quantum mechanics has a ceiling, and a strategy that reaches it can "
             "only be matched, never beaten. So the board lists everyone past each bar, in the order they got there.",
             ""]
    for key, emoji, _, many, rule in BARS:
        lines.append(f"{emoji} **{many}** ({rule}): {names(lists[key])}")
    reached = f"{percent(best)}, reached by {plural(len(at_best), 'builder')}" if best is not None else "nobody yet"
    lines += ["", f"**Best on the decaying chip:** {reached} (win rates only: every strategy stays sealed)", "",
              f"To get on it: seal your card with `{season.command.format(login='YOUR_GITHUB_USERNAME')}` in the "
              f"Season {season.number} pack, then send it with the [Season {season.number} entry form]({form_url}). "
              f"Your bars come back on your entry within minutes.", ""]
    return "\n".join(lines), json.dumps(data, indent=1, ensure_ascii=False) + "\n"
