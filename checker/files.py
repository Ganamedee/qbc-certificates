"""Checks on files in a member's repo that don't need running anything:
chart images, requirements.txt and pyproject.toml.
"""

from __future__ import annotations

import re
import struct

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MIN_CHART_BYTES = 5 * 1024
MAX_CHART_BYTES = 10 * 1024 * 1024
MIN_CHART_SIZE = (300, 200)

NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
EXTRAS = r"\[\s*" + NAME + r"(?:\s*,\s*" + NAME + r")*\s*\]"
VERSION = r"[A-Za-z0-9.*+!_-]+"
SPEC = r"(?:===|==|~=|!=|<=|>=|<|>)\s*" + VERSION
MARKER = r"[A-Za-z0-9_.'\"<>=!~ ()-]+"
REQUIREMENT_RE = re.compile(
    rf"(?P<name>{NAME})\s*(?P<extras>{EXTRAS})?\s*(?P<specs>{SPEC}(?:\s*,\s*{SPEC})*)?"
    rf"\s*(?:;\s*(?P<marker>{MARKER}))?")
MAX_REQUIREMENTS = 100


def png_size(data: bytes):
    """(width, height) of a PNG, or None if data isn't one."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def chart_problem(data: bytes) -> str:
    """Why a chart file can't be the project's chart, or "" if it can."""
    size = png_size(data)
    if size is None:
        return "not a PNG image"
    if len(data) > MAX_CHART_BYTES:
        return "bigger than 10 MB"
    if len(data) < MIN_CHART_BYTES or size[0] < MIN_CHART_SIZE[0] or size[1] < MIN_CHART_SIZE[1]:
        return f"too small to be a real chart ({size[0]}×{size[1]} pixels, {len(data)} bytes)"
    return ""


class Requirement:
    def __init__(self, name, extras, specs, marker):
        self.name, self.extras, self.specs, self.marker = name, extras, specs, marker

    @property
    def key(self) -> str:
        return re.sub(r"[-_.]+", "-", self.name).lower()

    @property
    def pinned(self) -> bool:
        return len(self.specs) == 1 and self.specs[0].startswith("==") \
            and not self.specs[0].startswith("===") and "*" not in self.specs[0]

    def line(self) -> str:
        text = self.name + (f"[{','.join(self.extras)}]" if self.extras else "") + ",".join(self.specs)
        return text + (f"; {self.marker}" if self.marker else "")


def parse_requirements(text: str):
    """(requirements, problems) for a requirements.txt. Only plain
    requirements are allowed: "name[extras] specifiers ; marker". Options
    (-r, -e, --index-url and the rest), URLs and paths are refused, because
    the checker installs these packages and must only take them from PyPI."""
    reqs, problems = [], []
    for number, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        match = REQUIREMENT_RE.fullmatch(line)
        if line.startswith("-") or "://" in line or " @ " in line or match is None:
            problems.append(f"requirements.txt line {number} isn't a plain package requirement: "
                            f"{line[:60]}")
            continue
        extras = [e.strip() for e in (match.group("extras") or "").strip("[] ").split(",") if e.strip()]
        specs = [re.sub(r"\s+", "", s) for s in (match.group("specs") or "").split(",") if s.strip()]
        reqs.append(Requirement(match.group("name"), extras, specs,
                                (match.group("marker") or "").strip()))
    if len(reqs) > MAX_REQUIREMENTS:
        problems.append(f"requirements.txt lists more than {MAX_REQUIREMENTS} packages")
    return reqs, problems


def unpinned(reqs) -> list:
    """Names of requirements not pinned to one exact version (name==1.2.3)."""
    return [r.name for r in reqs if not r.pinned]


def load_toml(text: str):
    """pyproject.toml as a dict, or None if it isn't valid TOML."""
    import tomllib  # Python 3.11+; the checker runs on 3.13
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None


def build_requirements(pyproject: dict) -> list:
    """[build-system] requires, as Requirements (None if any isn't plain)."""
    reqs = []
    for spec in (pyproject.get("build-system") or {}).get("requires") or []:
        if not isinstance(spec, str):
            return None
        parsed, problems = parse_requirements(spec)
        if problems or len(parsed) != 1:
            return None
        reqs += parsed
    return reqs


def project_dependencies(pyproject: dict) -> list:
    """[project] dependencies, as Requirements (None if any isn't plain)."""
    reqs = []
    for spec in (pyproject.get("project") or {}).get("dependencies") or []:
        if not isinstance(spec, str):
            return None
        parsed, problems = parse_requirements(spec)
        if problems or len(parsed) != 1:
            return None
        reqs += parsed
    return reqs
