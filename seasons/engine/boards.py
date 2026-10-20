"""The live boards: the top 10, the bars anyone can clear and the best score on each hole.

A builder's best scored entry is the one that counts: fewest strokes, and a tie goes to whoever got there first.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Season

TOP = 10


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


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


def rank_of(table: list, author_id: int, author: str) -> tuple:
    """(place, best entry) for this builder, or (None, None). The account id decides, so a renamed account keeps
    its place and a reused username doesn't inherit one."""
    for place, entry in enumerate(table, 1):
        if (entry.get("author_id") == author_id if author_id and entry.get("author_id")
                else entry["author"].casefold() == author.casefold()):
            return place, entry
    return None, None


def holes(root: Path, season: Season) -> list:
    """[(number, name, par)] from the season's hole files."""
    out = []
    for path in sorted((Path(root) / season.grader / "holes").glob("hole*.json"),
                       key=lambda p: int(p.stem[4:])):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append((int(data["hole"]), str(data["name"]), int(data["par"])))
    return out


def golf(season: Season, state: dict, root: Path, updated: str, form_url: str) -> tuple:
    """The board as (markdown, json text)."""
    table = standings(state)
    course = holes(root, season)
    par = sum(p for _, _, p in course)
    top = [{"rank": i, "builder": e["author"], "strokes": e["total"], "vs_par": e["total"] - par,
            "holes": e["solved"]} for i, e in enumerate(table[:TOP], 1)]
    birdies = sorted((e["author"] for e in table
                      if any((e["holes"].get(str(n)) is not None and e["holes"][str(n)] < p) for n, _, p in course)),
                     key=str.casefold)
    full = sorted((e["author"] for e in table if e["solved"] == len(course)), key=str.casefold)
    bests = []
    for n, name, p in course:
        scores = [e["holes"][str(n)] for e in table if e["holes"].get(str(n)) is not None]
        low = min(scores) if scores else None
        bests.append({"hole": n, "name": name, "par": p, "best": low,
                      "builders": scores.count(low) if scores else 0})
    data = {"season": season.number, "title": season.title, "updated": updated, "builders": len(table),
            "par": par, "top": top, "birdie_club": birdies, "full_card": full, "hole_bests": bests}

    lines = [f"# {season.title}, live board", "",
             f"{len(table)} builder{'s' if len(table) != 1 else ''} on the board · par {par} · updated {updated}", "",
             "Fewest strokes wins. Your best card counts, and a tie goes to whoever got there first. An unsolved "
             "hole counts as 2 × par.", ""]
    if top:
        lines += ["| # | Builder | Strokes | vs par | Holes |", "|---:|---|---:|---:|---:|"]
        lines += [f"| {t['rank']} | {t['builder']} | {t['strokes']} | {t['vs_par']:+d} | {t['holes']}/{len(course)} |"
                  for t in top]
    else:
        lines += ["Nobody on the board yet: the first card in takes the top spot."]
    lines += ["", f"🐦 **Birdie Club** (beat par on at least one hole): {', '.join(birdies) or 'nobody yet'}",
              f"⛳ **Full card** (all {len(course)} holes solved): {', '.join(full) or 'nobody yet'}", "",
              "**Best on each hole** (strokes only: every circuit stays sealed)", ""]
    for b in bests:
        lines.append(f"- Hole {b['hole']}, {b['name']} (par {b['par']}): " +
                     (f"{b['best']}, by {b['builders']} builder{'s' if b['builders'] != 1 else ''}"
                      if b["best"] is not None else "unsolved"))
    lines += ["", f"To get on it: seal your card with `python grade.py --card YOUR_GITHUB_USERNAME` in the "
                  f"Season {season.number} pack, then send it with the [Season {season.number} entry form]"
                  f"({form_url}). Your rank comes back on your entry within minutes.", ""]
    return "\n".join(lines), json.dumps(data, indent=1, ensure_ascii=False) + "\n"


KINDS = {1: golf}
