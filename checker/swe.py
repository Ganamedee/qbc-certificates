"""SWE-1: the member's Project 03 generator as a pip-installable package
with a `qrng` command and CI. The card's Acceptance checks: in a fresh
Python 3.13 environment `pip install . pytest`, `pytest -q`, and both
`qrng bits 128` commands print 128 zeros and ones; the latest Actions run
is green on two Python versions; src/ layout, type hints on the public API,
a CHANGELOG.md. (The README's "pseudo-random" line is a README check.)
"""

from __future__ import annotations

import ast
import posixpath
import re
from pathlib import Path

from . import files as filecheck
from . import rules
from .items import scan_item
from .pathway import PYTEST_PIN, _tail, py_sources, pytest_items
from .result import Item

LAYOUT = ("pyproject.toml", "src/qrng/__init__.py", "src/qrng/core.py", "src/qrng/cli.py")
VERSION_RE = re.compile(r"\b3\.\d{1,2}\b")


def swe1_items(ctx, repo, project) -> list:
    items = []
    missing = [p for p in LAYOUT if p not in repo.files]
    tests = [p for p in repo.files if p.startswith("tests/") and p.endswith(".py")
             and posixpath.basename(p).startswith("test")]
    detail = (f"missing {', '.join(missing)}" if missing else "") + \
        ("; no test files in tests/" if not tests else "")
    items.append(Item(not missing and bool(tests),
                      "src/ layout: pyproject.toml, src/qrng/ (__init__, core, cli) and tests/",
                      detail.strip("; ")))
    pyproject = filecheck.load_toml(repo.text("pyproject.toml") or "") \
        if "pyproject.toml" in repo.files else None
    scripts = ((pyproject or {}).get("project") or {}).get("scripts") or {}
    items.append(Item("qrng" in scripts, "pyproject.toml makes a qrng command",
                      "" if "qrng" in scripts else
                      "add qrng = \"qrng.cli:main\" under [project.scripts]" if pyproject is not None
                      else "pyproject.toml is missing or isn't valid TOML"))
    items.append(type_hints_item(repo))
    items.append(Item(bool(repo.find("CHANGELOG.md")), "Has a CHANGELOG.md"))
    items.append(actions_item(ctx, repo))
    sources = py_sources(repo)
    check = scan_item(sources, False, ())
    items.append(check)
    label = "pip install . pytest works (Python 3.13)"
    if not check.ok or pyproject is None or missing:
        return items + [Item(False, label, "not run until the items above pass")]
    build = filecheck.build_requirements(pyproject)
    deps = filecheck.project_dependencies(pyproject)
    if build is None or deps is None:
        return items + [Item(False, label, "pyproject.toml's build-system requires and dependencies "
                                           "must be plain package names and versions")]
    lines = [r.line() for r in (build or filecheck.parse_requirements("setuptools>=68")[0]) + deps]
    if "pytest" not in {r.key for r in deps}:
        lines.append(PYTEST_PIN)
    repo.checkout()
    ctx.sandbox.adopt(repo.path)
    python, done = ctx.sandbox.venv(project.key, lines, timeout=rules.INSTALL_MINUTES * 60)
    if done.code == 0 and not done.timed_out:
        # The package itself, built here with no network: its build code runs in the sandbox.
        done = ctx.sandbox.run([python, "-m", "pip", "install", "--quiet", "--no-deps",
                                "--no-build-isolation", "--no-index", str(repo.path)],
                               cwd=repo.path, timeout=rules.INSTALL_MINUTES * 60, python=python)
    if done.code or done.timed_out:
        return items + [Item(False, label, f"took longer than {rules.INSTALL_MINUTES} minutes"
                             if done.timed_out else _tail(done.output) or f"exit code {done.code}")]
    items.append(Item(True, label))
    ctx.sandbox.freeze(ctx.sandbox.root / "venvs" / project.key)
    outcome = ctx.sandbox.harness(python, ["pytest", str(repo.path), str(ctx.sandbox.ini_path)],
                                  cwd=repo.path, timeout=rules.TEST_MINUTES * 60)
    items += pytest_items(outcome, project, sources)
    command = str(Path(python).parent / "qrng")
    for args in (["bits", "128"], ["bits", "128", "--backend", "classical"]):
        run = ctx.sandbox.run([command, *args], cwd=repo.path, timeout=300, python=python)
        printed = any(re.fullmatch(r"[01]{128}", line.strip()) for line in run.output.splitlines())
        ok = run.code == 0 and not run.timed_out and printed
        items.append(Item(ok, f"qrng {' '.join(args)} prints exactly 128 zeros and ones",
                          "" if ok else "it timed out" if run.timed_out else
                          _tail(run.output) or f"exit code {run.code}"))
    return items


def type_hints_item(repo) -> Item:
    """Every public function and method in src/qrng/ has annotated
    parameters and a return type (__init__ may leave out the return)."""
    missing = []

    def check(fn, path, method=False):
        args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        args += [a for a in (fn.args.vararg, fn.args.kwarg) if a]
        if method and args and args[0].arg in ("self", "cls"):
            args = args[1:]
        if any(a.annotation is None for a in args) or (fn.returns is None and fn.name != "__init__"):
            missing.append(f"{posixpath.basename(path)}: {fn.name}")

    for path in sorted(p for p in repo.files if p.startswith("src/qrng/") and p.endswith(".py")):
        try:
            tree = ast.parse(repo.text(path) or "")
        except (SyntaxError, ValueError):
            missing.append(f"{posixpath.basename(path)}: can't be read")
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                check(node, path)
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                            (not sub.name.startswith("_") or sub.name == "__init__"):
                        check(sub, path, method=True)
    detail = ", ".join(missing[:4]) + (f" (and {len(missing) - 4} more)" if len(missing) > 4 else "")
    return Item(not missing, "Type hints on the public API", f"no type hints on {detail}" if missing else "")


def actions_item(ctx, repo) -> Item:
    """The latest finished run of some workflow on the default branch
    passed, with passing jobs on at least two Python 3 versions."""
    label = "The latest GitHub Actions run is green on two Python versions"
    latest = {}
    for run in ctx.gh.workflow_runs(repo.owner, repo.name, repo.branch):
        if run.get("status") == "completed":
            latest.setdefault(run.get("workflow_id"), run)
    if not latest:
        return Item(False, label, "no finished Actions runs on the default branch")
    newest = max(latest.values(), key=lambda r: r.get("created_at") or "")
    for run in sorted(latest.values(), key=lambda r: r.get("created_at") or "", reverse=True):
        if run.get("conclusion") != "success":
            continue
        versions = set()
        for job in ctx.gh.run_jobs(repo.owner, repo.name, run["id"]):
            found = VERSION_RE.search(job.get("name") or "")
            if job.get("conclusion") == "success" and found:
                versions.add(found.group(0))
        if len(versions) >= 2:
            return Item(True, label)
    return Item(False, label, f"the latest run of \"{newest.get('name', 'a workflow')}\" "
                              f"{'failed' if newest.get('conclusion') != 'success' else 'passed on fewer than two Python versions'}")
