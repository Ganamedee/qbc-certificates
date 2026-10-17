"""The club's own test files: the ladder projects' core tests and the build
cards' acceptance tests. The checker runs these copies, never the ones in a
member's repo, so editing a test can't make it pass.

Layout of a trusted folder (built from this repo by build(), and published
with the public claim forms repo):
  ladder/<key>/<core test file>        e.g. ladder/01/test_quantum_coin.py
  cards/<key>/<card test file>         e.g. cards/qml1/test_qml1.py
"""

from __future__ import annotations

import ast
import posixpath
import re
import shutil
from pathlib import Path

from . import rules

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_BLOCK_RE = re.compile(r"^```python\n(.*?)^```", re.M | re.S)


def test_names(source: str) -> list:
    """Top-level test functions, in the order the plain runner calls them."""
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test_") and node.name not in names:
            names.append(node.name)
    return names


def card_tests(markdown: str, first_test: str) -> str:
    """The acceptance-test code block of a build card: the Python block
    that defines first_test."""
    for block in CODE_BLOCK_RE.findall(markdown):
        if re.search(rf"^def {re.escape(first_test)}\(", block, re.M):
            return block
    raise ValueError(f"no code block defines {first_test}")


def ladder_path(trusted: Path, project) -> Path:
    return Path(trusted) / "ladder" / project.key / project.tests


def card_path(trusted: Path, project) -> Path:
    return Path(trusted) / "cards" / project.key / posixpath.basename(project.card_test_file)


def build(dest: Path, root: Path = REPO_ROOT) -> Path:
    """Copy the trusted tests out of this repo into dest."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    for project in rules.LADDER:
        target = ladder_path(dest, project)
        target.parent.mkdir(parents=True)
        shutil.copyfile(root / "projects" / project.folder / "tests" / project.tests, target)
    for project in rules.CARDS:
        if not project.card_test:
            continue
        markdown = (root / "skool" / "pathways" / project.card).read_text(encoding="utf-8")
        target = card_path(dest, project)
        target.parent.mkdir(parents=True)
        target.write_text(card_tests(markdown, project.card_test), encoding="utf-8")
    return dest


def check(trusted: Path) -> list:
    """Problems with a trusted folder: a file missing, or a different number
    of tests than rules.py expects (the test counts are part of the rules)."""
    problems = []
    for project in rules.LADDER + rules.CARDS:
        if project.ladder:
            path, expected = ladder_path(trusted, project), project.expected
        elif project.card_test:
            path, expected = card_path(trusted, project), project.card_expected
        else:
            continue
        if not path.is_file():
            problems.append(f"missing {path}")
            continue
        found = len(test_names(path.read_text(encoding="utf-8")))
        if found != expected:
            problems.append(f"{path} has {found} tests; rules.py expects {expected}")
    return problems
