"""Season 1's kind of season, Circuit Golf: fewest strokes wins.

A builder's best scored entry is the one that counts: fewest strokes, and a tie goes to whoever got there first.
The live board shows the top 10, the bars anyone can clear (the Birdie Club and the Full card) and the best score
on each hole.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .boards import TOP, names, ordinal, plural
from .config import Season

FIELDS = ("total", "par", "solved", "holes", "notes", "card")      # the scores an entry's result carries
EXTRA = ()                                                           # nothing more on a problem
WORDS = {"work": "holes", "public": "strokes", "back": "score and rank",
         "again": "Your best card counts: send a better one whenever you like, or edit this issue and paste it in."}
MAX_STROKES = 100_000
CARD = re.compile(r"[0-9A-F]{6}")
NOTES = {"wrong", "invalid", "unplayed", "crashed"}
PROBLEMS = {   # this season's own problem codes, and its wording for the shared ones
    "no-holes": "This card has no solved holes in it. Solve at least one, then make a new card.",
    "bad-holes": "This card doesn't match Season {n}'s holes. Make a new card with the latest Season {n} pack.",
    "bad-entry": "This card doesn't match Season {n}'s holes. Make a new card with the latest Season {n} pack.",
    "too-big": "One of the circuits in this card is too big to score. Shorten it, check it with `python grade.py`, "
               "then make a new card.",
    "timeout": "Scoring this card took too long. Check every hole with `python grade.py`, then make a new card.",
    "crashed": "Something in this card stopped the scorer. Check every hole with `python grade.py`, then make a new "
               "card.",
}


def holes(root: Path, season: Season) -> list:
    """[(number, name, par)] from the season's hole files."""
    out = []
    for path in sorted((Path(root) / season.grader / "holes").glob("hole*.json"),
                       key=lambda p: int(p.stem[4:])):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append((int(data["hole"]), str(data["name"]), int(data["par"])))
    return out


context = holes      # what check() and board() need to know about the season: its holes


def _int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def check(item: dict, course: list) -> dict | None:
    """A scored result's scores, if they add up for these holes, else None."""
    pars = {str(n): p for n, _, p in course or []}
    holes_, notes = item.get("holes"), item.get("notes")
    if not (pars and isinstance(holes_, dict) and set(holes_) == set(pars) and isinstance(notes, dict)
            and set(notes) <= set(pars) and all(v in NOTES for v in notes.values())):
        return None
    if not all(v is None or _int(v, 0, MAX_STROKES) for v in holes_.values()):
        return None
    solved = sum(v is not None for v in holes_.values())
    total = sum(v if v is not None else 2 * pars[k] for k, v in holes_.items())
    if set(notes) != {k for k, v in holes_.items() if v is None}:
        return None
    card = item.get("card")
    if not (item.get("total") == total and item.get("par") == sum(pars.values()) and item.get("solved") == solved
            and isinstance(card, str) and (card == "none" if solved == 0 else bool(CARD.fullmatch(card)))):
        return None
    return {"total": total, "par": item["par"], "solved": solved,
            "holes": {k: holes_[k] for k in sorted(holes_, key=int)}, "notes": dict(sorted(notes.items())),
            "card": card}


def extra(item: dict) -> dict:
    return {}


def problem_text(season: Season, result: dict) -> str | None:
    text = PROBLEMS.get(result.get("problem"))
    return text.format(n=season.number) if text else None


def standings(state: dict) -> list:
    """Each builder's best scored entry, best first."""
    best = {}
    for number, entry in state.get("issues", {}).items():
        if entry.get("outcome") != "scored":
            continue
        who = entry.get("author_id") or entry["author"].casefold()
        mine = {**entry, "issue": int(number)}
        held = best.get(who)
        if held is None or (mine["total"], mine["time"], mine["issue"]) < (held["total"], held["time"], held["issue"]):
            best[who] = mine
    return sorted(best.values(), key=lambda e: (e["total"], e["time"], e["issue"]))


table = standings


def rank_of(standing: list, author_id: int, author: str) -> tuple:
    """(place, best entry) for this builder, or (None, None). The account id decides, so a renamed account keeps
    its place and a reused username doesn't inherit one."""
    for place, entry in enumerate(standing, 1):
        if (entry.get("author_id") == author_id if author_id and entry.get("author_id")
                else entry["author"].casefold() == author.casefold()):
            return place, entry
    return None, None


def scored_lines(season: Season, result: dict, repo: str, standing: list) -> list:
    n, number = season.number, result["issue"]
    form = f"[Season {n} entry form]({season.form_url(repo)})"
    diff = result["total"] - result["par"]
    per_hole = " · ".join(f"H{k} {v}" if v is not None else f"H{k} unsolved" for k, v in result["holes"].items())
    lines = [f"{season.emoji} **Season {n}, scored: {result['total']} strokes** (par {result['par']}, {diff:+d}), "
             f"{result['solved']} of {len(result['holes'])} holes solved, card {result['card']}.", "", per_hole, ""]
    board = f"[live board]({season.board_url(repo)})"
    place, best = rank_of(standing or [], result["author_id"], result["author"])
    count = len(standing or [])
    if best and best["issue"] == number:
        lines.append(f"This is your best card: **{ordinal(place)} of {count}** on the {board} right now.")
    elif best:
        lines.append(f"Your best card is still #{best['issue']}, with {best['total']} strokes: "
                     f"**{ordinal(place)} of {count}** on the {board} right now.")
    command = season.command.format(login=result["author"])
    lines += ["", f"Beat it whenever you like: improve a hole, run `{command}` again and send the new card with the "
                  f"{form}, or edit this issue and paste it in. Your circuits stay sealed, and only your strokes are "
                  f"public."]
    return lines


def board(season: Season, state: dict, course: list, updated: str, form_url: str) -> tuple:
    """The live board as (markdown, json text)."""
    standing = standings(state)
    par = sum(p for _, _, p in course)
    top = [{"rank": i, "builder": e["author"], "strokes": e["total"], "vs_par": e["total"] - par,
            "holes": e["solved"]} for i, e in enumerate(standing[:TOP], 1)]
    birdies = sorted((e for e in standing
                      if any((e["holes"].get(str(n)) is not None and e["holes"][str(n)] < p) for n, _, p in course)),
                     key=lambda e: e["author"].casefold())
    full = sorted((e for e in standing if e["solved"] == len(course)), key=lambda e: e["author"].casefold())
    bests = []
    for n, name, p in course:
        scores = [e["holes"][str(n)] for e in standing if e["holes"].get(str(n)) is not None]
        low = min(scores) if scores else None
        bests.append({"hole": n, "name": name, "par": p, "best": low,
                      "builders": scores.count(low) if scores else 0})
    data = {"season": season.number, "title": season.title, "updated": updated, "builders": len(standing),
            "par": par, "top": top, "birdie_club": [e["author"] for e in birdies],
            "full_card": [e["author"] for e in full], "hole_bests": bests}

    lines = [f"# {season.title}, live board", "",
             f"{plural(len(standing), 'builder')} on the board · par {par} · updated {updated}", "",
             "Fewest strokes wins. Your best card counts, and a tie goes to whoever got there first. An unsolved "
             "hole counts as 2 × par.", ""]
    if top:
        lines += ["| # | Builder | Strokes | vs par | Holes |", "|---:|---|---:|---:|---:|"]
        lines += [f"| {t['rank']} | {t['builder']} | {t['strokes']} | {t['vs_par']:+d} | {t['holes']}/{len(course)} |"
                  for t in top]
    else:
        lines += ["Nobody on the board yet: the first card in takes the top spot."]
    lines += ["", f"🐦 **Birdie Club** (beat par on at least one hole): {names(birdies)}",
              f"⛳ **Full card** (all {len(course)} holes solved): {names(full)}", "",
              "**Best on each hole** (strokes only: every circuit stays sealed)", ""]
    for b in bests:
        lines.append(f"- Hole {b['hole']}, {b['name']} (par {b['par']}): " +
                     (f"{b['best']}, by {plural(b['builders'], 'builder')}" if b["best"] is not None else "unsolved"))
    lines += ["", f"To get on it: seal your card with `{season.command.format(login='YOUR_GITHUB_USERNAME')}` in "
                  f"the Season {season.number} pack, then send it with the [Season {season.number} entry form]"
                  f"({form_url}). Your rank comes back on your entry within minutes.", ""]
    return "\n".join(lines), json.dumps(data, indent=1, ensure_ascii=False) + "\n"
