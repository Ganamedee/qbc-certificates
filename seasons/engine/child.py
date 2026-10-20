"""Scores one opened season entry in a clean process.

The score job starts it as `python -I child.py --grader DIR --season N`, with no secrets and no token in its
environment, sends the entry's scoring part as JSON on stdin and reads one JSON line from stdout. That line holds
scores only, never the entry. Every season's entries are OpenQASM 2 text, which its grader parses: no member code
runs.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

MAX_INPUT = 300_000


def golf(grader: Path, entry: dict) -> dict:
    sys.path.insert(0, str(grader))
    import grade  # the season's own grader, the same file as in the member pack

    holes = grade.load_holes(grader)
    raw = entry.get("holes")
    if not isinstance(raw, dict) or not raw:
        return {"ok": False, "problem": "no-holes"}
    blocks = {}
    for key, qasm in raw.items():
        if not (isinstance(key, str) and key.isdigit() and int(key) in holes and isinstance(qasm, str)):
            return {"ok": False, "problem": "bad-holes"}
        if len(qasm) > grade.MAX_QASM_CHARS:
            return {"ok": False, "problem": "too-big"}
        blocks[int(key)] = qasm
    results = [grade.grade_qasm(h, blocks[n]) if n in blocks else grade.Result(h, "unplayed", message="not submitted")
               for n, h in holes.items()]
    solved = {r.hole.number: r.qasm for r in results if r.status == "solved"}
    return {
        "ok": True,
        "total": sum(r.counted for r in results),
        "par": sum(h.par for h in holes.values()),
        "solved": len(solved),
        "holes": {str(r.hole.number): (r.strokes if r.status == "solved" else None) for r in results},
        "notes": {str(r.hole.number): r.status for r in results if r.status != "solved"},
        "card": grade.card_code(solved) if solved else "none",
    }


def chsh(grader: Path, entry: dict) -> dict:
    sys.path.insert(0, str(grader))
    import referee  # the season's own referee, the same file as in the member pack

    raw = entry.get("strategy")
    if not isinstance(raw, dict) or not raw:
        return {"ok": False, "problem": "no-strategy"}
    if set(raw) != set(referee.PARTS) or not all(isinstance(v, str) for v in raw.values()):
        return {"ok": False, "problem": "bad-parts"}
    circuits = {}
    for part in referee.PARTS:
        try:
            circuits[part] = referee.circuit_from_qasm(part, raw[part])
        except referee.EntryError:
            return {"ok": False, "problem": "bad-circuit", "part": part}
    return {"ok": True, "perfect": referee.rate(referee.score(circuits, "perfect").win),
            "decaying": referee.rate(referee.score(circuits, "decaying").win)}


KINDS = {1: golf, 2: chsh}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="score one opened season entry")
    ap.add_argument("--grader", required=True, type=Path)
    ap.add_argument("--season", required=True, type=int)
    args = ap.parse_args(argv)
    text = sys.stdin.read(MAX_INPUT + 1)
    try:
        if len(text) > MAX_INPUT:
            raise ValueError
        entry = json.loads(text)
        if not isinstance(entry, dict):
            raise ValueError
    except ValueError:
        out = {"ok": False, "problem": "bad-entry"}
    else:
        noise = io.StringIO()
        try:
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                out = KINDS[args.season](args.grader.resolve(), entry)
        except Exception:  # one strange entry must never stop the run, and its details stay here
            out = {"ok": False, "problem": "crashed"}
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
