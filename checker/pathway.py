"""Pathway build cards (FIN, OPT, QML, RES, SEC, SWE): each card's
Acceptance checks, run the way the card says. In a fresh Python 3.13
environment: install requirements.txt, run the build script if the card has
one, then the tests. QML-1, RES-1 and RES-2 give members their tests, so the
club's copy of those runs; FIN, OPT, SEC and SWE members write their own, so
their suite runs and the tests the card names must be in it and pass.
"""

from __future__ import annotations

import ast
import posixpath
import re

from . import files as filecheck
from . import readme, rules, scan, trusted
from .items import HARMLESS_NAMES, scan_item, tests_item
from .result import Item

PYTEST_PIN = "pytest==9.1.1"
MEMO_WORDS = 1_200
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def check_card(ctx, repo, project, request) -> list:
    if project.key == "swe1":
        return swe1(ctx, repo, project)
    items = []
    if project.key == "fin2" and not request.repos.get("fin4"):
        items += memo_items(ctx, repo, in_fin2=True)
    if "res2" in project.special:
        items += report_items(ctx, repo)
    text = repo.text("requirements.txt")
    req_label = "requirements.txt lists what to install"
    if text is None:
        return items + [Item(False, req_label, "no requirements.txt at the top of the repo")]
    reqs, problems = filecheck.parse_requirements(text)
    if problems or not reqs:
        return items + [Item(False, req_label, problems[0] if problems else "it's empty")]
    if "sec1" in project.special:
        items.append(crypto_item(reqs))

    sources = py_sources(repo)
    if project.card_test:
        card_file = trusted.card_path(ctx.trusted, project)
        card_source = card_file.read_text(encoding="utf-8")
        reach = reachable(repo, card_source)
        names = {p.split("/")[0].removesuffix(".py") for p in reach}
        check = scan_item({p: sources[p] for p in reach}, True, names)
        label = f"of the card's acceptance tests pass ({project.card_test_file})"
        expected = trusted.test_names(card_source)
    else:
        check = scan_item(sources, False, ())
        label = "python -m pytest -q passes"
        names, expected = set(), []
    items.append(check)
    if not check.ok:
        return items + [Item(False, label, "not run until the safety read-through passes")]

    lines = [r.line() for r in reqs]
    if not project.card_test and "pytest" not in {r.key for r in reqs}:
        lines.append(PYTEST_PIN)
    repo.checkout()
    ctx.sandbox.adopt(repo.path)
    python, ok, install = install_items(ctx, project.key, lines,
                                        "pip install -r requirements.txt works (Python 3.13)")
    items.append(install)
    if not ok:
        return items
    clash = [n for n in scan.shadows(names, ctx.sandbox.installed_modules(python))
             if n not in HARMLESS_NAMES]
    if clash:
        return items + [Item(False, "Your files' names don't hide installed packages",
                             f"{clash[0]} is also the name of a package requirements.txt "
                             "installs, so it would be imported in its place: rename it")]
    if project.build:
        built = run_script(ctx, repo, python, project.build)
        items.append(built)
        if not built.ok:
            return items
    if project.card_test:
        outcome = ctx.sandbox.harness(python, ["file", str(card_file),
                                               str(repo.path / project.card_test_file)],
                                      cwd=repo.path, timeout=rules.TEST_MINUTES * 60)
        items.append(tests_item(outcome, expected, label))
    else:
        outcome = ctx.sandbox.harness(python, ["pytest", str(repo.path), str(ctx.sandbox.ini_path)],
                                      cwd=repo.path, timeout=rules.TEST_MINUTES * 60)
        items += pytest_items(outcome, project, sources)
    if "sec1" in project.special:
        items.append(benchmark_item(ctx, repo, python))
    return items


def py_sources(repo) -> dict:
    return {p: repo.text(p) or "" for p in sorted(repo.files)
            if p.endswith(".py") and not re.match(r"(\.?venv|env|site-packages)/", p)}


def reachable(repo, source: str) -> set:
    """The member's files a trusted test imports, directly or through each
    other: top-level modules and packages next to the README."""
    local = {}
    for path in repo.files:
        if not path.endswith(".py"):
            continue
        top = path.split("/")[0]
        if "/" not in path:
            local.setdefault(top[:-3], []).append(path)
        elif f"{top}/__init__.py" in repo.files:
            local.setdefault(top, []).append(path)
    todo, seen = [n for n in scan.local_imports(source) if n in local], set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for path in local[name]:
            todo += [m for m in scan.local_imports(repo.text(path) or "") if m in local]
    return {p for name in seen for p in local[name]}


def install_items(ctx, name: str, lines: list, label: str):
    python, done = ctx.sandbox.venv(name, lines, timeout=rules.INSTALL_MINUTES * 60)
    ok = done.code == 0 and not done.timed_out
    if ok:
        ctx.sandbox.freeze(ctx.sandbox.root / "venvs" / name)
        return python, True, Item(True, label)
    detail = (f"took longer than {rules.INSTALL_MINUTES} minutes" if done.timed_out
              else _tail(done.output) or f"pip exited with code {done.code}")
    return python, False, Item(False, label, detail + ". The checker installs ready-made wheels "
                                                    "for Python 3.13 only")


def run_script(ctx, repo, python: str, script: str) -> Item:
    label = f"python {script} runs"
    if script not in repo.files:
        return Item(False, label, f"no {script} at the top of the repo")
    done = ctx.sandbox.run([python, "-B", script], cwd=repo.path,
                           timeout=rules.BUILD_MINUTES * 60, python=python)
    if done.timed_out:
        return Item(False, label, f"it took longer than {rules.BUILD_MINUTES} minutes")
    return Item(done.code == 0, label, "" if done.code == 0 else
                _tail(done.output) or f"it exited with code {done.code}")


def _tail(output: str) -> str:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def pytest_items(outcome: dict, project, sources: dict) -> list:
    if outcome.get("timed_out"):
        return [Item(False, "python -m pytest -q passes",
                     f"the tests took longer than {rules.TEST_MINUTES} minutes")]
    tests, errors = outcome.get("tests") or [], outcome.get("errors") or []
    passed = [t for t in tests if t.get("outcome") == "passed"]
    failed = [t for t in tests if t.get("outcome") == "failed"]
    suite_ok = outcome.get("exit") == 0 and not failed and not errors and bool(passed)
    if suite_ok:
        detail = ""
    elif errors:
        detail = errors[0]
    elif failed:
        detail = "; ".join(f"{t['name']}: {t.get('message', '')[:150]}" for t in failed[:2])
    else:
        detail = "no tests found" if not tests else f"pytest exited with code {outcome.get('exit')}"
    items = [Item(suite_ok, "python -m pytest -q passes", detail)]
    if project.member_tests:
        found = {}
        for t in tests:
            found.setdefault(t["name"].split("::")[-1].split("[")[0], []).append(t)
        missing = [n for n in project.member_tests if n not in found]
        failing = [n for n in project.member_tests
                   if n in found and not all(t.get("outcome") == "passed" for t in found[n])]
        empty = [n for n in project.member_tests if n in found and not has_assertion(n, sources)]
        problems = ([f"missing {', '.join(missing)}"] if missing else []) + \
                   ([f"not passing: {', '.join(failing)}"] if failing else []) + \
                   ([f"no assert in {', '.join(empty)}"] if empty else [])
        items.append(Item(not problems, f"The card's {len(project.member_tests)} named tests "
                                        "are there and pass", "; ".join(problems)))
    if project.min_tests:
        items.append(Item(len(passed) >= project.min_tests,
                          f"At least {project.min_tests} tests pass", f"{len(passed)} passed"
                          if len(passed) < project.min_tests else ""))
    return items


def has_assertion(name: str, sources: dict) -> bool:
    """Whether some test function called name checks something: an assert,
    an assert_* call or pytest.raises."""
    for source in sources.values():
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Assert):
                        return True
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        called = func.attr if isinstance(func, ast.Attribute) else \
                            getattr(func, "id", "")
                        if called.startswith("assert") or called == "raises":
                            return True
    return False


def crypto_item(reqs) -> Item:
    label = "requirements.txt has cryptography 48 or newer (or liboqs-python)"
    for r in reqs:
        if r.key == "liboqs-python":
            return Item(True, label)
        if r.key == "cryptography":
            versions = [re.match(r"(?:==|>=|~=|===)(\d+)", s) for s in r.specs]
            if any(m and int(m.group(1)) >= 48 for m in versions):
                return Item(True, label)
            return Item(False, label, "pin cryptography 48 or newer, e.g. cryptography==50.0.2")
    return Item(False, label, "neither is in requirements.txt")


def benchmark_item(ctx, repo, python: str) -> Item:
    label = "python benchmark.py writes sizes.csv and pqc_results.png"
    if "benchmark.py" not in repo.files:
        return Item(False, label, "no benchmark.py at the top of the repo")
    for name in ("sizes.csv", "pqc_results.png"):
        if (repo.path / name).exists():
            ctx.sandbox.remove(repo.path / name)
    run = run_script(ctx, repo, python, "benchmark.py")
    if not run.ok:
        return Item(False, label, run.detail)
    missing = [n for n in ("sizes.csv", "pqc_results.png") if not ctx.sandbox.exists(repo.path / n)]
    return Item(not missing, label, f"it didn't write {' or '.join(missing)}" if missing else "")


# ── Documents: the FIN-4 memo and the RES-2 report ───────────────────────────
def pdf_info(ctx, repo, path: str):
    """{"pages", "text", "links"} for a PDF in the repo, read in the sandbox."""
    data = repo.read(path, 20_000_000)
    if data is None:
        return None
    copy = ctx.sandbox.root / "req" / f"doc-{repo.name[:40]}-{posixpath.basename(path)}"
    copy.write_bytes(data)
    copy.chmod(0o644)
    outcome = ctx.sandbox.harness(ctx.sandbox.club_python, ["pdf", str(copy)],
                                  cwd=ctx.sandbox.root, timeout=120)
    return None if outcome.get("timed_out") or outcome.get("pdf_error") else outcome


def report_items(ctx, repo) -> list:
    label = "report/report.pdf is at most 2 pages"
    if "report/report.pdf" not in repo.files:
        return [Item(False, label, "report/report.pdf isn't in the repo")]
    info = pdf_info(ctx, repo, "report/report.pdf")
    if info is None:
        return [Item(False, label, "the checker couldn't read it as a PDF")]
    return [Item(info["pages"] <= 2, label, f"it has {info['pages']} pages" if info["pages"] > 2 else "")]


def find_memo(repo):
    top = repo.find("memo.md", "memo.pdf")
    if top:
        return top
    found = sorted((p for p in repo.files if posixpath.basename(p).lower() in ("memo.md", "memo.pdf")),
                   key=lambda p: (p.count("/"), p))
    return found[0] if found else None


def memo_items(ctx, repo, in_fin2: bool) -> list:
    """FIN-4: memo.md or memo.pdf with at most two pages, four or more linked
    sources including Herman et al. and Chakrabarti et al., and the speedup
    called quadratic. In the FIN-2 repo, its README links the memo."""
    path = find_memo(repo)
    if path is None:
        return [Item(False, "Has the FIN-4 memo (memo.md or memo.pdf)", "not found in this repo")]
    items = [Item(True, f"Has the FIN-4 memo ({path})")]
    if path.lower().endswith(".pdf"):
        info = pdf_info(ctx, repo, path)
        if info is None:
            return items + [Item(False, "The memo can be read", "the checker couldn't read the PDF")]
        text, links = info["text"], set(info["links"]) | set(URL_RE.findall(info["text"]))
        items.append(Item(info["pages"] <= 2, "The memo is at most two pages",
                          f"{info['pages']} pages" if info["pages"] > 2 else ""))
    else:
        text = repo.text(path, 1_000_000) or ""
        links = set(URL_RE.findall(text))
        count = len(readme.words(text))
        items.append(Item(count <= MEMO_WORDS, "The memo is at most two pages (about 1,000 words)",
                          f"{count} words" if count > MEMO_WORDS else ""))
    items.append(Item(len(links) >= 4, "The memo links at least 4 sources",
                      f"{len(links)} links found" if len(links) < 4 else ""))
    missing = [n for n in ("Herman", "Chakrabarti") if not re.search(n, text, re.I)]
    items.append(Item(not missing, "The memo cites Herman et al. and Chakrabarti et al.",
                      f"no {' or '.join(missing)}" if missing else ""))
    items.append(Item(bool(re.search(r"quadratic", text, re.I)),
                      'The memo qualifies the speedup ("quadratic")'))
    if in_fin2:
        doc_path = repo.find("README.md", "README.markdown")
        doc = readme.Doc(repo.text(doc_path) or "") if doc_path else readme.Doc("")
        linked = path in readme.links(doc, "", repo.owner, repo.name)
        items.append(Item(linked, "README links the memo", "" if linked else f"add a link to {path}"))
    return items


# ── SWE-1: the pip-installable QRNG package ──────────────────────────────────
def swe1(ctx, repo, project) -> list:
    from .swe import swe1_items
    return swe1_items(ctx, repo, project)
