"""Runs inside the checker's sandbox, as the sandbox user, with no network.
Nothing imports this module: the checker copies it into a folder the
sandbox can read but not change, and runs it with the right Python.

  python -I -B harness.py file   TRUSTED_TEST AS_PATH [QBC_TARGET]
  python -I -B harness.py pytest ROOT INI [TEST_PATH ...]
  python -I -B harness.py pdf    FILE

file: runs one of the club's test files (kept outside the member's repo, so
it can't be edited) as if it were the member's AS_PATH, calling each test
function the way the test file's own plain-Python runner does.
pytest: runs the member's own pytest suite from ROOT, with the club's empty
INI instead of the member's settings.
pdf: counts the pages of a PDF in the member's repo and reads its text and
links (pypdf parses untrusted files, so it runs here too).

It first reads a key from stdin, then hides the original stdout from the
code under test and prints one line there at the end:
    QBC-RESULT <json> <HMAC-SHA256 of the json with the key>
so output printed by the member's code can't pass for a result.
"""

import ast
import os
import signal
import sys
import time
import types

try:
    from _hashlib import hmac_digest as _hmac_digest
except ImportError:  # pragma: no cover - CPython always has it
    import hmac as _hmac
    _hmac_digest = _hmac.digest

TEST_SECONDS = 300
_write, _exit, _time = os.write, os._exit, time.monotonic
_alarm, _on_signal, _SIGALRM = signal.alarm, signal.signal, signal.SIGALRM


def _json(value) -> str:
    """JSON for the result, written here so the member's code can't patch it."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(round(value, 3) if isinstance(value, float) else int(value))
    if isinstance(value, str):
        out = []
        for ch in value:
            code = ord(ch)
            if ch in '"\\':
                out.append("\\" + ch)
            elif 32 <= code < 127:
                out.append(ch)
            elif code < 0x10000:
                out.append("\\u%04x" % code)
            else:
                code -= 0x10000
                out.append("\\u%04x\\u%04x" % (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)))
        return '"' + "".join(out) + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_json(v) for v in value) + "]"
    return "{" + ",".join(_json(str(k)) + ":" + _json(v) for k, v in value.items()) + "}"


def _short(text, limit=300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _describe(error) -> str:
    try:
        return _short(f"{type(error).__name__}: {error}")
    except BaseException:  # a member's exception whose str() itself fails
        return "an exception that can't be shown"


class _Timeout(BaseException):
    pass


def run_file(trusted: str, as_path: str, target: str) -> dict:
    """The club's test file, run against the member's code."""
    with open(trusted, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test_") and node.name not in names:
            names.append(node.name)
    code = compile(source, as_path, "exec", dont_inherit=True)
    originals = {c.co_name: c for c in code.co_consts if isinstance(c, types.CodeType)}
    if target:
        os.environ["QBC_TARGET"] = target
    sys.argv = [as_path]
    namespace = {"__name__": "qbc_trusted_tests", "__file__": as_path}
    started = _time()
    try:
        exec(code, namespace)
    except BaseException as error:  # the member's code didn't import, or exited
        return {"mode": "file", "tests": [{"name": n, "ok": False, "message": ""} for n in names],
                "import_error": _describe(error), "seconds": _time() - started}
    tests = [(name, namespace.get(name)) for name in names]
    results = []

    def alarm(signum, frame):
        raise _Timeout()

    _on_signal(_SIGALRM, alarm)
    for name, fn in tests:
        if not isinstance(fn, types.FunctionType) or fn.__code__ is not originals.get(name):
            results.append({"name": name, "ok": False, "message": "the test function was replaced"})
            continue
        _alarm(TEST_SECONDS)
        try:
            fn()
            ok, message = True, ""
        except _Timeout:
            ok, message = False, f"took longer than {TEST_SECONDS} seconds"
        except BaseException as error:
            ok, message = False, _describe(error)
        finally:
            _alarm(0)
        if ok and fn.__code__ is not originals.get(name):
            ok, message = False, "the test function was replaced"
        results.append({"name": name, "ok": ok, "message": message})
    return {"mode": "file", "tests": results, "import_error": "", "seconds": _time() - started}


def run_pytest(root: str, ini: str, paths: list) -> dict:
    """The member's own pytest suite, as `python -m pytest -q` from ROOT."""
    import pytest

    class Collector:
        def __init__(self):
            self.tests, self.errors = {}, []

        def pytest_runtest_logreport(self, report):
            entry = self.tests.setdefault(report.nodeid, {"name": report.nodeid, "ok": True,
                                                          "outcome": "passed", "message": ""})
            if report.failed:
                entry.update(ok=False, outcome="failed", message=_short(report.longreprtext))
            elif report.skipped and entry["ok"]:
                entry.update(ok=False, outcome="skipped", message="")

        def pytest_collectreport(self, report):
            if report.failed:
                self.errors.append(_short(f"{report.nodeid}: {report.longreprtext}"))

    collector = Collector()
    sys.path.insert(0, root)
    started = _time()
    try:
        exit_code = int(pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", root, "-c", ini,
                                     *(paths or [root])], plugins=[collector]))
    except BaseException as error:
        return {"mode": "pytest", "exit": -1, "tests": list(collector.tests.values()),
                "errors": collector.errors + [_describe(error)], "seconds": _time() - started}
    return {"mode": "pytest", "exit": exit_code, "tests": list(collector.tests.values()),
            "errors": collector.errors, "seconds": _time() - started}


def read_pdf(path: str) -> dict:
    """Pages, text and link targets of a PDF (the first five pages' text)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = len(reader.pages)
        texts, links = [], []
        for page in list(reader.pages)[:5]:
            try:
                texts.append(page.extract_text() or "")
                for annot in page.get("/Annots") or []:
                    uri = (annot.get_object().get("/A") or {}).get("/URI")
                    if uri:
                        links.append(str(uri)[:300])
            except Exception:
                continue
    except Exception as error:
        return {"mode": "pdf", "pdf_error": _describe(error)}
    return {"mode": "pdf", "pages": pages, "text": " ".join(texts)[:50000], "links": links[:200]}


def main() -> None:
    key = sys.stdin.buffer.read(64)
    os.close(0)
    sys.stdin = open(os.devnull)
    result_fd = os.dup(1)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
    try:
        mode, args = sys.argv[1], sys.argv[2:]
        if mode == "file":
            result = run_file(args[0], args[1], args[2] if len(args) > 2 else "")
        elif mode == "pytest":
            result = run_pytest(args[0], args[1], args[2:])
        elif mode == "pdf":
            result = read_pdf(args[0])
        else:
            result = {"mode": mode, "harness_error": "unknown mode"}
    except BaseException as error:
        result = {"mode": "error", "harness_error": _describe(error)}
    body = _json(result).encode("ascii")
    _write(result_fd, b"QBC-RESULT " + body + b" " + _hmac_digest(key, body, "sha256").hex().encode()
           + b"\n")
    _exit(0)


if __name__ == "__main__":
    main()
