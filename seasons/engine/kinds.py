"""Each kind of season: how its results are checked, what its comments say and what its live board shows.

A kind is a module with FIELDS, EXTRA, WORDS, context(), check(), extra(), problem_text(), table(), scored_lines()
and board(). A kind can also have more outcomes than scored, problem, waiting and withdrawn: OUTCOMES (each one's
fields that the state keeps), check_outcome() and outcome_lines(), with ONE_SET if each account's first scored entry
is its only one (Season 3), and form() for an entry form of its own. Every one is standard library only, like the
rest of the engine.
"""

from __future__ import annotations

from . import chsh, detective, eve, golf
from .config import Season

KINDS = {"golf": golf, "chsh": chsh, "detective": detective, "eve": eve}


def of(season: Season):
    return KINDS[season.kind]
