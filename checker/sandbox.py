"""Where members' code runs: as a separate user with no password, no sudo
and no home of the runner's, in its own process, IPC and (except while pip
installs packages) network namespace, with limits on processes and file
sizes. The checker's own files and the club's tests sit in a folder that
user can read but not change. setup() also closes the runner's own folders
to it (the checker's code, the actions that run after the check) and makes
sure it can't change any Python it runs, since GitHub's runners make their
tool cache, where actions/setup-python puts Python, writable by everyone.

Layout (on a GitHub runner, made by setup()):
  /opt/qbc/          the runner's: harness.py, pytest.ini, trusted/, venv/ (the club's stack)
  /srv/qbc/work/     one folder per repo, handed to the sandbox user
  /srv/qbc/venvs/    pathway repos' own environments, frozen after install
  /srv/qbc/home, tmp the sandbox user's home and temp folder

unsafe=True runs everything as yourself with none of that, for trying the
checker on your own machine. Never use it on members' repos.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import rules

HARNESS = Path(__file__).with_name("harness.py")
NPROC = 512
FSIZE = 2 * 1024 ** 3
OUTPUT_LIMIT = 4 * 1024 ** 2
# Passed on to installs (the only runs with the network) so pip works behind
# a proxy. GitHub's runners set none of them.
NET_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
           "PIP_INDEX_URL", "PIP_CERT")


class SandboxError(Exception):
    """The sandbox itself failed: the checker's problem, not the member's."""


@dataclass
class Completed:
    code: int
    output: str          # stdout and stderr together, the last part only
    timed_out: bool = False
    seconds: float = 0.0


class Sandbox:
    def __init__(self, *, root="/srv/qbc", opt="/opt/qbc", user="qbcrun", unsafe=False,
                 club_python=None, base_python=None):
        self.root, self.opt, self.user, self.unsafe = Path(root), Path(opt), user, unsafe
        self._club_python = club_python
        self.base_python = base_python or shutil.which("python3.13") or shutil.which("python3")
        self.sudo = [] if os.geteuid() == 0 else ["sudo", "-n"]

    # ── Setting up ──────────────────────────────────────────────────────────
    @property
    def club_python(self) -> str:
        return self._club_python or str(self.opt / "venv" / "bin" / "python")

    @property
    def harness_path(self) -> Path:
        return self.opt / "harness.py"

    @property
    def ini_path(self) -> Path:
        return self.opt / "pytest.ini"

    def setup(self, trusted_dir: Path) -> None:
        """Make the user, the folders and the club's Python environment, and
        copy the harness and the club's tests where the sandbox can't change them."""
        me = f"{os.getuid()}:{os.getgid()}"
        if not self.unsafe:
            if subprocess.run(["id", "-u", self.user], capture_output=True).returncode:
                self._root_cmd(["useradd", "--system", "--user-group", "--no-create-home",
                                "--home-dir", str(self.root / "home"), "--shell",
                                "/usr/sbin/nologin", self.user])
            self._root_cmd(["install", "-d", "-m", "755", "-o", "root", "-g", "root", str(self.root)])
            for name in ("work", "venvs", "req"):
                self._root_cmd(["install", "-d", "-m", "755", "-o", me.split(":")[0],
                                "-g", me.split(":")[1], str(self.root / name)])
            for name in ("home", "tmp"):
                self._root_cmd(["install", "-d", "-m", "700", "-o", self.user, "-g", self.user,
                                str(self.root / name)])
            self._root_cmd(["install", "-d", "-m", "755", "-o", me.split(":")[0],
                            "-g", me.split(":")[1], str(self.opt)])
        else:
            for name in ("work", "venvs", "req", "home", "tmp"):
                (self.root / name).mkdir(parents=True, exist_ok=True)
            self.opt.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HARNESS, self.harness_path)
        self.ini_path.write_text("[pytest]\n")
        if (self.opt / "trusted").exists():
            shutil.rmtree(self.opt / "trusted")
        shutil.copytree(trusted_dir, self.opt / "trusted")
        if not self._club_python:
            subprocess.run([self.base_python, "-m", "venv", str(self.opt / "venv")], check=True)
            subprocess.run([self.club_python, "-m", "pip", "install", "--quiet", "--no-input",
                            "--disable-pip-version-check", "--only-binary=:all:",
                            *rules.CLUB_REQUIREMENTS], check=True)
        if not self.unsafe:
            self.lock()

    def runner_only(self) -> list:
        """Folders only the runner needs, which lock() closes to the sandbox
        user: the runner's home, the GitHub Actions work folder (the claims
        repo's checkout, the actions that run after the check and their temp
        files) and the checker's own folder, unless one holds the sandbox's."""
        folders = {Path.home().resolve(), Path(__file__).resolve().parent.parent}
        if os.environ.get("RUNNER_WORKSPACE"):
            folders.add(Path(os.environ["RUNNER_WORKSPACE"]).resolve().parent)
        if os.environ.get("RUNNER_TEMP"):
            folders.add(Path(os.environ["RUNNER_TEMP"]).resolve())
        needed = (self.opt.resolve(), self.root.resolve())
        return sorted(f for f in folders if f.is_dir() and not any(n.is_relative_to(f) for n in needed))

    @staticmethod
    def python_paths(python: str) -> list:
        """The folders a Python imports from, its interpreter's and its shared
        libraries' folders, and the files it has loaded."""
        done = subprocess.run([python, "-I", "-c", PYTHON_PATHS], capture_output=True, text=True)
        if done.returncode:
            raise SandboxError(f"couldn't ask {python} where it imports from: {done.stderr.strip()[-300:]}")
        return [Path(p) for p in json.loads(done.stdout) if p]

    def read_paths(self) -> list:
        """Folders the sandbox reads or runs code from but must never change:
        /opt/qbc, and every folder the club's Python, the base Python and the
        checker itself import from."""
        paths = {self.opt, Path(os.path.realpath(sys.executable)).parent, *(Path(p) for p in sys.path if p),
                 *self.python_paths(self.club_python), *self.python_paths(self.base_python)}
        return sorted({p.resolve() for p in paths if p.exists()})

    def lock(self) -> None:
        """Close the runner's own folders to the sandbox user, and take away its
        write access to everything in, or above, the folders it reads (the
        sandbox's own folders aside). Whatever the runner's umask, selftest()
        then proves it."""
        private = self.runner_only()
        for folder in private:
            self._root_cmd(["chmod", "o-rwx", str(folder)])
        read = [p for p in self.read_paths() if not any(p.is_relative_to(f) for f in private)]
        trees = [p for p in read if not any(p != q and p.is_relative_to(q) for q in read)]
        if trees:
            self._root_cmd(["find", *map(str, trees), "!", "-type", "l", "-perm", "-0002",
                            "-exec", "chmod", "o-w", "{}", "+"])
        for parent in sorted({a for p in [*read, self.root.resolve()] for a in p.parents}):
            mode = parent.stat().st_mode
            if mode & stat.S_IWOTH and not mode & stat.S_ISVTX:    # a sticky folder, like /tmp, is fine
                self._root_cmd(["chmod", "o-w", str(parent)])

    def _root_cmd(self, argv) -> None:
        done = subprocess.run(self.sudo + list(argv), capture_output=True, text=True)
        if done.returncode:
            raise SandboxError(f"{argv[0]} failed: {done.stderr.strip()[:300]}")

    def work_dir(self, name: str) -> Path:
        path = self.root / "work" / name
        if path.exists():
            self.remove(path)
        path.mkdir(parents=True)
        return path

    def adopt(self, path: Path) -> None:
        """Hand a folder to the sandbox user."""
        if not self.unsafe:
            self._root_cmd(["chown", "-R", f"{self.user}:{self.user}", str(path)])

    def freeze(self, path: Path) -> None:
        """Take a folder back from the sandbox user: readable, not changeable.
        Also drops .pth files that run code and any sitecustomize, so an
        installed package can't load code into every Python start-up."""
        for pth in Path(path).rglob("*.pth"):
            try:
                text = pth.read_text(errors="replace")
            except OSError:
                continue
            if any(line.lstrip().startswith("import") for line in text.splitlines()):
                self._remove_file(pth)
        for custom in list(Path(path).rglob("sitecustomize.py")) + list(Path(path).rglob("usercustomize.py")):
            self._remove_file(custom)
        if not self.unsafe:
            self._root_cmd(["chown", "-R", "root:root", str(path)])
            self._root_cmd(["chmod", "-R", "go-w", str(path)])

    @staticmethod
    def installed_modules(python: str) -> set:
        """The top-level names a virtual environment's packages can be
        imported by (numpy, PIL, rustworkx, ...), or an empty set if python
        isn't in one."""
        names = set()
        for site in Path(python).parent.parent.glob("lib/python3*/site-packages"):
            for entry in site.iterdir():
                name = entry.name
                if name == "__pycache__" or name.endswith((".dist-info", ".egg-info", ".pth")):
                    continue
                if entry.is_dir():
                    names.add(name)
                elif name.endswith((".py", ".so", ".pyd")):
                    names.add(name.split(".")[0])
        return names

    def _remove_file(self, path: Path) -> None:
        if self.unsafe:
            path.unlink()
        else:
            self._root_cmd(["rm", "-f", str(path)])

    def remove(self, path: Path) -> None:
        if self.unsafe:
            shutil.rmtree(path, ignore_errors=True)
        else:
            self._root_cmd(["rm", "-rf", "--one-file-system", str(path)])

    def exists(self, path: Path, min_bytes: int = 1) -> bool:
        """Whether the sandbox wrote a non-empty file at path."""
        if self.unsafe:
            return path.is_file() and path.stat().st_size >= min_bytes
        done = subprocess.run(self.sudo + ["stat", "-c", "%s %F", str(path)],
                              capture_output=True, text=True)
        size, _, kind = done.stdout.strip().partition(" ")
        return done.returncode == 0 and kind == "regular file" and int(size or 0) >= min_bytes

    def read(self, path: Path, limit: int = 1_000_000) -> bytes:
        if self.unsafe:
            with open(path, "rb") as handle:
                return handle.read(limit)
        done = subprocess.run(self.sudo + ["head", "-c", str(limit), str(path)], capture_output=True)
        return done.stdout if done.returncode == 0 else b""

    def cleanup(self) -> None:
        """Stop anything the sandbox user left running."""
        if not self.unsafe:
            subprocess.run(self.sudo + ["pkill", "-KILL", "-u", self.user], capture_output=True)

    # ── Running ─────────────────────────────────────────────────────────────
    def env(self, python: str, extra=None) -> dict:
        env = {"PATH": f"{Path(python).parent}:/usr/local/bin:/usr/bin:/bin",
               "HOME": str(self.root / "home"), "TMPDIR": str(self.root / "tmp"),
               "LANG": "C.UTF-8", "MPLBACKEND": "Agg",
               "MPLCONFIGDIR": str(self.root / "tmp" / "matplotlib"),
               "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
               "PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
               "PIP_CACHE_DIR": str(self.root / "home" / "pip-cache")}
        env.update(extra or {})
        return env

    def command(self, argv, env: dict, net: bool) -> list:
        if self.unsafe:
            return list(argv)
        isolate = ["unshare", "--ipc", "--pid", "--fork", "--kill-child", "--mount-proc"]
        if not net:
            isolate.append("--net")
        return (self.sudo + ["prlimit", f"--nproc={NPROC}", f"--fsize={FSIZE}", "--nofile=4096", "--"]
                + isolate + ["--", "setpriv", f"--reuid={self.user}", f"--regid={self.user}",
                             "--clear-groups", "--no-new-privs", "--inh-caps=-all",
                             "--bounding-set=-all", "--", "env", "-i"]
                + [f"{k}={v}" for k, v in sorted(env.items())] + list(argv))

    def run(self, argv, *, cwd: Path, timeout: float, python: str, net: bool = False,
            stdin: bytes = b"", env=None) -> Completed:
        """Run argv in the sandbox. Output goes to a file, not memory, so a
        flood of it can't hurt; only the end is kept."""
        environment = self.env(python, env)
        if net:
            environment.update((k, v) for k, v in os.environ.items() if k in NET_ENV)
        started = time.monotonic()
        with tempfile.TemporaryFile() as out:
            proc = subprocess.Popen(self.command(argv, environment, net), cwd=str(cwd),
                                    stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT,
                                    env=environment if self.unsafe else {"PATH": "/usr/bin:/bin"})
            timed_out = False
            try:
                proc.communicate(stdin, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()
            finally:
                self.cleanup()
            out.seek(0, os.SEEK_END)
            size = out.tell()
            out.seek(max(0, size - OUTPUT_LIMIT))
            output = out.read().decode("utf-8", "replace")
        return Completed(proc.returncode, output, timed_out, time.monotonic() - started)

    def harness(self, python: str, args, *, cwd: Path, timeout: float, env=None) -> dict:
        """Run the harness and return its result, checked against the key
        only it was given. A missing or forged result line is an error."""
        key = secrets.token_hex(32)
        done = self.run([python, "-I", "-B", str(self.harness_path), *args], cwd=cwd,
                        timeout=timeout, python=python, stdin=key.encode(), env=env)
        if done.timed_out:
            return {"timed_out": True, "seconds": done.seconds}
        found = []
        for line in done.output.splitlines():
            if line.startswith("QBC-RESULT "):
                body, _, mac = line[len("QBC-RESULT "):].rpartition(" ")
                expected = hmac.new(key.encode(), body.encode(), "sha256").hexdigest()
                if hmac.compare_digest(mac, expected):
                    found.append(body)
        if len(found) != 1:
            raise SandboxError(f"the test run gave {len(found)} valid results "
                               f"(exit {done.code}): {done.output[-500:]}")
        result = json.loads(found[0])
        if result.get("harness_error"):
            raise SandboxError(f"the harness failed: {result['harness_error']}")
        return result

    def venv(self, name: str, requirements, *, timeout: float) -> tuple:
        """A fresh environment for one pathway repo, with its requirements
        installed from PyPI as ready-made wheels only (nothing builds or runs
        setup code), then frozen. (python path, Completed of the install)."""
        path = self.root / "venvs" / name
        if path.exists():
            self.remove(path)
        path.mkdir(parents=True)
        req = self.root / "req" / f"{name}.txt"
        req.write_text("".join(line + "\n" for line in requirements))
        req.chmod(0o644)
        self.adopt(path)
        python = str(path / "bin" / "python")
        made = self.run([self.base_python, "-m", "venv", str(path)], cwd=self.root,
                        timeout=120, python=self.base_python, net=False)
        if made.code:
            raise SandboxError(f"couldn't make a virtual environment: {made.output[-300:]}")
        done = self.run([python, "-m", "pip", "install", "--quiet", "--only-binary=:all:",
                         "-r", str(req)], cwd=self.root, timeout=timeout,
                        python=python, net=True)
        return python, done

    # ── Proving the sandbox works ───────────────────────────────────────────
    def selftest(self) -> list:
        """Problems with the sandbox itself, or []. Runs a small test file of
        the checker's own: one test that passes, one that fails, and (unless
        unsafe) proof that the sandbox has no network, can't read the runner's
        files and can't change the club's."""
        secret = Path(tempfile.mkdtemp()) / "secret.txt"
        secret.write_text("runner only\n")
        secret.chmod(0o600)
        checks = SELFTEST_TESTS
        for name, value in (("__OPT__", str(self.opt.resolve())), ("__ROOT__", str(self.root.resolve())),
                            ("__SECRET__", str(secret)),
                            ("__RUNNER_ONLY__", [str(f) for f in self.runner_only()])):
            checks = checks.replace(name, json.dumps(value))
        test = self.opt / "trusted" / "selftest.py"
        test.parent.mkdir(parents=True, exist_ok=True)
        test.write_text(checks)
        test.chmod(0o644)
        work = self.work_dir("selftest")
        (work / "tests").mkdir()
        self.adopt(work)
        try:
            result = self.harness(self.club_python, ["file", str(test), str(work / "tests" / "t.py")],
                                  cwd=work, timeout=120)
        except SandboxError as error:
            return [str(error)]
        finally:
            shutil.rmtree(secret.parent, ignore_errors=True)
        outcomes = {t["name"]: t["ok"] for t in result.get("tests", [])}
        expected = {"test_passes": True, "test_fails": False}
        if not self.unsafe:
            expected.update(test_no_network=True, test_cannot_read_runner_files=True,
                            test_cannot_change_club_files=True, test_alone=True)
        return [f"selftest {name}: expected {'pass' if want else 'fail'}, got "
                f"{'pass' if outcomes.get(name) else 'fail'}"
                for name, want in expected.items() if outcomes.get(name) is not want]


PYTHON_PATHS = '''
import json, os, sys, sysconfig
import _hashlib  # loads the C libraries the harness uses for its signature
lines = open("/proc/self/maps").read().splitlines() if os.path.exists("/proc/self/maps") else []
loaded = {line.split(None, 5)[5] for line in lines if len(line.split(None, 5)) == 6}
print(json.dumps(sys.path + [os.path.dirname(os.path.realpath(sys.executable)), sysconfig.get_config_var("LIBDIR") or ""]
                 + sorted(p for p in loaded if p.startswith("/") and os.path.exists(p))))
'''

SELFTEST_TESTS = '''
import os
import socket
import stat
import sys
import sysconfig

OPT, ROOT, SECRET, RUNNER_ONLY = __OPT__, __ROOT__, __SECRET__, __RUNNER_ONLY__


def test_passes():
    assert 1 + 1 == 2


def test_fails():
    assert 1 + 1 == 3, "this one is meant to fail"


def test_no_network():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    except OSError:
        return
    raise AssertionError("the sandbox reached the internet")


def test_cannot_read_runner_files():
    try:
        open(SECRET).read()
    except OSError:
        pass
    else:
        raise AssertionError("the sandbox read a file only the runner may read")
    # The runner's home, the checker's own code and the actions that run after the check.
    seen = [f for f in RUNNER_ONLY if os.access(f, os.R_OK) or os.access(f, os.X_OK)]
    assert not seen, f"the sandbox can look inside {seen[0]}"


def changeable(path):
    return not os.path.islink(path) and os.access(path, os.W_OK)


def above(path):
    """The folders above path, except sticky ones (like /tmp), where only a file's owner can replace it."""
    while path != os.path.dirname(path):
        path = os.path.dirname(path)
        if not os.stat(path).st_mode & stat.S_ISVTX:
            yield path


def loaded():
    """The files this process has loaded: libpython, extension modules, C libraries."""
    with open("/proc/self/maps") as handle:
        lines = [line.split(None, 5) for line in handle.read().splitlines()]
    return {line[5] for line in lines if len(line) == 6 and line[5].startswith("/")}


def test_cannot_change_club_files():
    # The harness, the club's tests, every folder this Python imports from, its interpreter, its shared
    # libraries and the files it has loaded, and the folders above them all: changing any of them could
    # change what a later run, or the checker itself, does.
    found = {p for p in (ROOT, *(os.path.join(ROOT, n) for n in ("work", "venvs", "req")), *above(ROOT))
             if os.path.exists(p) and changeable(p)}
    tops = sorted({os.path.realpath(t) for t in (OPT, *sys.path, os.path.dirname(os.path.realpath(sys.executable)),
                                                 sysconfig.get_config_var("LIBDIR"), *loaded())
                   if t and os.path.exists(t)})
    for i, top in enumerate(tops):
        if top.startswith(ROOT + os.sep) or any(top.startswith(t + os.sep) for t in tops[:i]):
            continue
        found.update(p for p in (top, *above(top)) if changeable(p))
        for folder, dirs, files in os.walk(top):
            found.update(p for p in (os.path.join(folder, n) for n in dirs + files) if changeable(p))
    assert not found, f"the sandbox can change {len(found)} of the club's files, e.g. {sorted(found)[:3]}"


def test_alone():
    pids = [p for p in os.listdir("/proc") if p.isdigit()]
    assert len(pids) < 10, f"the sandbox sees {len(pids)} processes"
'''
