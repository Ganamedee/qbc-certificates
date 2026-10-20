"""A member's repo, cloned once: its files are read straight from Git (so
nothing in the repo runs), then it's checked out for the sandbox.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
MAX_REPO_KB = 300_000
MAX_FILES = 20_000
MAX_COMMITS = 2_000
CLONE_SECONDS = 300
# Files Python would run in place of the source the checker read: bytecode (a
# .pyc in __pycache__ can stand in for its .py) and native modules (found
# before a .py of the same name). No club project has any, so the checkout
# drops them, and any matplotlibrc, which can point matplotlib at other code.
COMPILED = (".pyc", ".pyo", ".so", ".pyd")
DROPPED_NAMES = ("__pycache__", "matplotlibrc")
# No hooks, no symlinks, no local or ext transports, no credential helpers,
# and none of the runner's own Git settings (Git LFS included).
GIT = ["git", "-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null",
       "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
       "-c", "core.fsmonitor=false", "-c", "credential.helper=", "-c", "core.quotePath=false",
       "-c", "submodule.recurse=false", "-c", "advice.detachedHead=false"]


class RepoError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    mode: str
    kind: str
    sha: str
    size: int
    path: str


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    authored: str
    committed: str
    files: list


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TMPDIR", "HTTPS_PROXY",
                                                       "https_proxy", "SSL_CERT_FILE", "NO_PROXY")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_ASKPASS="true", HOME=os.devnull)
    if os.environ.get("GIT_SSL_CAINFO"):
        env["GIT_SSL_CAINFO"] = os.environ["GIT_SSL_CAINFO"]
    return env


class Repo:
    def __init__(self, path: Path, owner: str, name: str, branch: str):
        self.path, self.owner, self.name, self.branch = Path(path), owner, name, branch
        self.entries = self._tree()
        self.files = {e.path for e in self.entries.values() if e.kind == "blob"}

    def git(self, *args, binary=False, limit=50_000_000, timeout=120):
        done = subprocess.run(GIT + ["-C", str(self.path), *args], capture_output=True,
                              env=_env(), timeout=timeout)
        if done.returncode:
            raise RepoError(done.stderr.decode("utf-8", "replace").strip()[:300])
        return done.stdout[:limit] if binary else done.stdout[:limit].decode("utf-8", "replace")

    def _tree(self) -> dict:
        raw = self.git("ls-tree", "-r", "-l", "-z", "--full-tree", "HEAD", binary=True)
        entries = {}
        for record in raw.split(b"\0"):
            if not record:
                continue
            meta, _, path = record.partition(b"\t")
            mode, kind, sha, size = meta.decode().split()
            name = path.decode("utf-8", "replace")
            entries[name] = Entry(mode, kind, sha, int(size) if size.isdigit() else 0, name)
            if len(entries) > MAX_FILES:
                raise RepoError(f"the repo has more than {MAX_FILES} files")
        return entries

    def read(self, path: str, limit: int = 2_000_000):
        """A file's bytes from Git, or None if it isn't a file in the repo."""
        entry = self.entries.get(path)
        if entry is None or entry.kind != "blob" or entry.mode == "120000":
            return None
        if entry.size > limit:
            return None
        return self.git("cat-file", "blob", entry.sha, binary=True, limit=limit)

    def text(self, path: str, limit: int = 2_000_000):
        data = self.read(path, limit)
        return None if data is None else data.decode("utf-8", "replace")

    def find(self, *names) -> str | None:
        """The first of names (top-level, any letter case) that's a file."""
        lower = {p.lower(): p for p in self.files if "/" not in p}
        return next((lower[n.lower()] for n in names if n.lower() in lower), None)

    def text_at(self, sha: str, names, limit: int = 1_000_000):
        """The first of names (top-level, any letter case) as it was in commit
        sha, or None: no such file then, or no such commit in this clone (a
        force push can drop one)."""
        if not SHA_RE.fullmatch(sha or ""):
            return None
        try:
            raw = self.git("ls-tree", "-l", "-z", sha, binary=True)
        except RepoError:
            return None
        blobs = {}
        for record in raw.split(b"\0"):
            meta, _, path = record.partition(b"\t")
            mode, kind, blob, size = (meta.decode().split() + ["", "", "", ""])[:4]
            if kind == "blob" and mode != "120000" and size.isdigit() and int(size) <= limit:
                blobs.setdefault(path.decode("utf-8", "replace").lower(), blob)
        blob = next((blobs[n.lower()] for n in names if n.lower() in blobs), None)
        return None if blob is None else self.git("cat-file", "blob", blob, limit=limit)

    def history(self) -> list:
        out = self.git("log", "--no-color", "--no-renames", f"--max-count={MAX_COMMITS}",
                       "--format=%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%cI", "--name-only", "HEAD")
        commits = []
        for chunk in out.split("\x1e"):
            lines = chunk.strip("\n").split("\n")
            if not lines or not lines[0]:
                continue
            fields = lines[0].split("\x1f")
            if len(fields) == 5:
                commits.append(Commit(*fields, files=[f for f in lines[1:] if f]))
        return commits

    def checkout(self) -> list:
        """Put the files on disk for the sandbox, then drop .git and the
        compiled files (COMPILED). Returns the paths dropped."""
        self.git("reset", "--quiet", "--hard", "HEAD", timeout=CLONE_SECONDS)
        shutil.rmtree(self.path / ".git")
        dropped = []
        for path in sorted(self.path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.name in DROPPED_NAMES or path.suffix.lower() in COMPILED:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                dropped.append(path.relative_to(self.path).as_posix())
        return dropped


def clone(owner: str, name: str, branch: str, dest: Path) -> Repo:
    url = f"https://github.com/{owner}/{name}.git"
    try:
        done = subprocess.run(GIT + ["clone", "--quiet", "--no-checkout", "--single-branch",
                                     "--no-tags", "--branch", branch, "--", url, str(dest)],
                              capture_output=True, env=_env(), timeout=CLONE_SECONDS)
    except subprocess.TimeoutExpired:
        raise RepoError("downloading the repo took too long") from None
    if done.returncode:
        raise RepoError(done.stderr.decode("utf-8", "replace").strip()[:300])
    return Repo(dest, owner, name, branch)
