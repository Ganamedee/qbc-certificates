"""Each kind of season: how its results are checked, what its comments say and what its live board shows.

A kind is a module with FIELDS, EXTRA, WORDS, context(), check(), extra(), problem_text(), table(), scored_lines()
and board(). Every one is standard library only, like the rest of the engine.
"""

from __future__ import annotations

from . import chsh, golf
from .config import Season

KINDS = {"golf": golf, "chsh": chsh}


def of(season: Season):
    return KINDS[season.kind]
