"""A read-through of members' Python before the club's tests run it.

The club's tests import the member's code into the same Python process, so
code written to fool the tests (patch them, reach into the test runner, or
rewrite the library functions the tests compare against) could make a
failing project pass. Ordinary project code never needs any of the things
below, so the checker refuses code that uses them and says which line. It's
a tripwire, not a proof: the repos are public, and a certificate earned by
tricking the checker is revoked.

strict=False is for repos whose own pytest suite is the check (FIN, OPT, SEC,
SWE): their tests may use subprocess and friends, so only tampering with
pytest itself is refused there.
"""

from __future__ import annotations

import ast
import sys

DENIED_MODULES = frozenset((
    "inspect", "gc", "ctypes", "_ctypes", "cffi", "importlib", "builtins", "__main__",
    "threading", "_thread", "multiprocessing", "subprocess", "socket", "atexit", "signal",
    "faulthandler", "marshal", "code", "codeop", "pty", "pickle", "dill", "cloudpickle",
    "unittest.mock", "trace", "pdb", "sysconfig", "site", "runpy", "pkgutil", "zipimport"))
PYTEST_MODULES = frozenset(("_pytest", "pluggy"))
# Attributes no project code needs: frames, function internals, the import
# machinery and the ways into Python's builtins.
DENIED_ATTRS = frozenset((
    "_getframe", "f_back", "f_globals", "f_locals", "f_builtins", "f_code", "f_trace",
    "tb_frame", "gi_frame", "cr_frame", "ag_frame", "__globals__", "__code__", "__closure__",
    "__defaults__", "__kwdefaults__", "__subclasses__", "__builtins__", "__import__",
    "__dict__", "__self__", "__func__", "__wrapped__", "__setattr__", "__delattr__",
    "__getattribute__", "settrace", "setprofile", "addaudithook", "_current_frames",
    "attrgetter"))
PYTEST_ATTRS = frozenset(("hookimpl", "hookwrapper"))
# The same, but only on sys and os, where they reach the interpreter itself
# or start, stop and write past the process the tests run in.
SYS_OS_ATTRS = frozenset((
    "modules", "meta_path", "path_hooks", "path_importer_cache", "excepthook", "displayhook",
    "monitoring", "_exit", "dup", "dup2", "write", "fdopen", "kill", "killpg", "abort", "system",
    "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp", "execl", "execle", "execlp",
    "execlpe", "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe"))
DENIED_CALLS = frozenset(("exec", "eval", "compile", "__import__", "globals", "breakpoint",
                          "vars", "locals", "setattr", "delattr"))
DENIED_STRINGS = ("/proc", "/dev/fd", "qbc-result", "QBC-RESULT", "qbc_harness")
MAX_FILE_BYTES = 1_000_000


def scan(source: str, filename: str, strict: bool = True) -> list:
    """Every problem in one file, as "file:line: what"."""
    if len(source) > MAX_FILE_BYTES:
        return [f"{filename}: too big to check ({len(source)} bytes)"]
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as error:
        line = getattr(error, "lineno", None) or 0
        return [f"{filename}:{line}: Python can't read this file ({error.__class__.__name__})"]
    modules = DENIED_MODULES | PYTEST_MODULES if strict else PYTEST_MODULES
    attrs = DENIED_ATTRS | PYTEST_ATTRS if strict else PYTEST_ATTRS
    imported, module_of = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = a.asname or a.name.split(".")[0]
                imported.add(name)
                module_of[name] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)
    problems = []

    def deny(node, what):
        problems.append(f"{filename}:{getattr(node, 'lineno', 0)}: {what}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _denied(alias.name, modules):
                    deny(node, f"imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and _denied(module, modules):
                deny(node, f"imports from {module}")
            for alias in node.names:
                if node.level == 0 and module and not _denied(module, modules) \
                        and _denied(f"{module}.{alias.name}", modules):     # from unittest import mock
                    deny(node, f"imports {module}.{alias.name}")
                elif alias.name in attrs or (strict and node.level == 0 and module in ("sys", "os")
                                             and alias.name in SYS_OS_ATTRS):
                    deny(node, f"imports {alias.name}")
        elif isinstance(node, ast.Attribute) and (node.attr in attrs or (
                strict and node.attr in SYS_OS_ATTRS and isinstance(node.value, ast.Name)
                and module_of.get(node.value.id, node.value.id) in ("sys", "os"))):
            deny(node, f"uses .{node.attr}")
        elif strict and isinstance(node, ast.Name) and node.id in attrs:
            deny(node, f"uses {node.id}")
        elif strict and isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in DENIED_CALLS:
            deny(node, f"calls {node.func.id}()")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("pytest_"):
            deny(node, f"defines the pytest hook {node.name}")
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for sub in (s for t in targets for s in ast.walk(t)):
                if isinstance(sub, ast.Name) and sub.id == "pytest_plugins":
                    deny(node, "sets pytest_plugins")
                elif strict and isinstance(sub, ast.Attribute) and (
                        _root_name(sub) in imported or isinstance(_base(sub), ast.Call)):
                    deny(node, f"changes {_dotted(sub)}, which belongs to imported code")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and (
                any(s in node.value for s in DENIED_STRINGS) or (strict and node.value in attrs)):
            deny(node, f"mentions {node.value[:40]!r}")
    return sorted(set(problems), key=_line_order)


def _denied(name: str, modules) -> bool:
    parts = name.split(".")
    return any(".".join(parts[:n]) in modules for n in range(1, len(parts) + 1))


def _base(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node


def _root_name(node):
    node = _base(node)
    return node.id if isinstance(node, ast.Name) else None


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _line_order(problem: str):
    name, _, rest = problem.partition(":")
    line = rest.partition(":")[0]
    return (name, int(line) if line.isdigit() else 0, problem)


def local_imports(source: str) -> set:
    """Top-level names a file imports (import x, from x import y), so the
    checker can follow imports into the member's other modules."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def shadows(module_names, installed=()) -> list:
    """Member modules named like a standard-library module or an installed
    package (random.py, numpy.py): Python would import them in its place."""
    taken = set(getattr(sys, "stdlib_module_names", ())) | {n.lower() for n in installed}
    return sorted(n for n in module_names if n in taken or n.lower() in taken)


def scan_sources(sources: dict, strict: bool = True) -> list:
    """Problems across {path: source}."""
    problems = []
    for path in sorted(sources):
        problems += scan(sources[path], path, strict)
    return problems
