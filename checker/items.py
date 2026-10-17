"""Report lines shared by the ladder and the pathway checks."""

from __future__ import annotations

from . import rules, scan
from .result import Item
from .sandbox import SandboxError

# The club stack's top-level modules: a member file with one of these names
# would be imported instead of the real thing.
CLUB_PACKAGES = ("qiskit", "qiskit_aer", "matplotlib", "numpy", "scipy", "pytest", "pypdf",
                 "rustworkx", "sympy", "PIL", "pandas")
HARMLESS_NAMES = ("this", "antigravity")


def scan_item(sources: dict, strict: bool, modules=(), installed=()) -> Item:
    """The safety read-through of sources. modules are the names the member's
    files and folders can be imported by; installed, the environment's own
    (the club's stack when not given)."""
    problems = scan.scan_sources(sources, strict)
    clash = [n for n in scan.shadows(modules, installed or CLUB_PACKAGES) if n not in HARMLESS_NAMES]
    problems += [f"{n} has the name of a Python module, so it would be imported in its place: "
                 "rename it" for n in clash]
    detail = "; ".join(problems[:3]) + (f" (and {len(problems) - 3} more)" if len(problems) > 3 else "")
    return Item(not problems, "Your code passes the checker's safety read-through", detail)


def tests_item(outcome: dict, expected: list, what: str) -> Item:
    """N/N <what>, from a harness file-mode result."""
    if outcome.get("timed_out"):
        return Item(False, f"0/{len(expected)} {what}",
                    f"the tests took longer than {rules.TEST_MINUTES} minutes")
    tests = outcome.get("tests") or []
    if [t.get("name") for t in tests] != expected:
        raise SandboxError("the test run didn't report the tests it was given")
    passed = sum(1 for t in tests if t.get("ok") is True)
    failed = [t for t in tests if t.get("ok") is not True]
    if outcome.get("import_error"):
        detail = f"your code didn't load: {outcome['import_error']}"
    else:
        detail = "; ".join(f"{t['name']}: {t.get('message') or 'failed'}" for t in failed[:3])
        if len(failed) > 3:
            detail += f" (and {len(failed) - 3} more)"
    return Item(passed == len(expected), f"{passed}/{len(expected)} {what}", detail)
