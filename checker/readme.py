"""What a README must have, read roughly the way GitHub shows it: headings
(# and underlined), fenced code, images, links and the template's [bracketed]
placeholders. Only the Markdown the checks need, nothing more.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from urllib.parse import unquote, urlsplit

from .result import Item

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
SETEXT_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
NOT_PARAGRAPH_RE = re.compile(r"^\s*(?:[-*+>|]|\d+[.)]\s|$)")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?[^)]*\)")
IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\](?:\[([^\]]*)\])?(?!\()")
LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?[^)]*\)")
LINK_REF_RE = re.compile(r"(?<!!)\[([^\]]+)\]\[([^\]]*)\]")
REF_DEF_RE = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+.*)?$", re.M)
HTML_IMG_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1", re.I | re.S)
HTML_LINK_RE = re.compile(r"<a\b[^>]*?\bhref\s*=\s*([\"'])(.*?)\1", re.I | re.S)
URL_RE = re.compile(r"<?\bhttps?://[^\s<>)\]]+>?")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
PLACEHOLDER_RE = re.compile(r"(?<![!\\])\[([^\[\]\n]{20,})\](?![(\[:])")
WORD_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
COMMAND_RE = re.compile(
    r"(?:^|&&|;|\|\|?)\s*(?:\$\s+|>\s+|PS[^>]*>\s*)?(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
    r"(?:python(?:3(?:\.\d+)?)?|py(?:\s+-3(?:\.\d+)?)?|pip3?|pytest|qrng|uv|jupyter)(?:\.exe)?\b",
    re.I | re.M)

SECTIONS = {
    "added": (r"what i(?:'ve|’ve| have)? added|my (?:own )?extension|personal extension",
              '"What I added"', 12),
    "limits": (r"honest limits|limitations|\blimits\b|threat model|caveats", '"Honest limits"', 12),
    "portfolio": (r"portfolio|about this project", "portfolio paragraph", 30),
    "where": (r"where quantum helps", '"Where quantum helps and where it doesn\'t"', 12),
}
NOTICES = {
    "security": (r"security notice|not cryptographically secure", "the security notice at the top"),
    "educational": (r"educational simulation", 'the "Educational simulation" notice at the top'),
}


class Doc:
    """A Markdown file split into lines, with its code blocks and headings."""

    def __init__(self, text: str):
        self.text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.lines = self.text.split("\n")
        self.code = [False] * len(self.lines)
        self.headings = []          # (first line, level, title, first body line)
        fence = None
        for i, line in enumerate(self.lines):
            mark = FENCE_RE.match(line)
            if fence:
                self.code[i] = True
                if mark and mark.group(1)[0] == fence[0] and len(mark.group(1)) >= len(fence) \
                        and not line.strip()[len(mark.group(1)):].strip():
                    fence = None
                continue
            if mark:
                fence, self.code[i] = mark.group(1), True
                continue
            atx = ATX_RE.match(line)
            if atx:
                self.headings.append((i, len(atx.group(1)), _title(atx.group(2) or ""), i + 1))
            elif SETEXT_RE.match(line) and i and not self.code[i - 1] \
                    and not NOT_PARAGRAPH_RE.match(self.lines[i - 1]) \
                    and not (self.headings and self.headings[-1][0] == i - 1):
                level = 1 if line.strip()[0] == "=" else 2
                self.headings.append((i - 1, level, _title(self.lines[i - 1]), i + 1))

    def prose_lines(self, start=0, end=None, code=False) -> str:
        """The text of lines start..end, without headings, and without code
        blocks (or with them, but not their fences, if code)."""
        heads = {h[0] for h in self.headings} | {h[3] - 1 for h in self.headings}
        end = len(self.lines) if end is None else end
        return "\n".join(self.lines[i] for i in range(start, end) if i not in heads
                         and not (FENCE_RE.match(self.lines[i]) if code else self.code[i]))

    def section(self, pattern: str, code=False):
        """The body of the first heading matching pattern, subsections
        included, or None."""
        for n, (_, level, title, body) in enumerate(self.headings):
            if re.search(pattern, title, re.I):
                end = next((h[0] for h in self.headings[n + 1:] if h[1] <= level), len(self.lines))
                return self.prose_lines(body, end, code)
        return None

    def top(self) -> str:
        """Everything above the first ## heading: where notices belong."""
        first = next((h[0] for h in self.headings if h[1] >= 2), min(len(self.lines), 20))
        return "\n".join(self.lines[:first])

    def outside_code(self) -> str:
        return "\n".join("" if self.code[i] else line for i, line in enumerate(self.lines))

    def code_text(self) -> str:
        """Code blocks and inline code: where run instructions live."""
        blocks = [line for i, line in enumerate(self.lines) if self.code[i]]
        spans = [m.group(2) for m in INLINE_CODE_RE.finditer(self.outside_code())]
        return "\n".join(blocks + spans)


def _title(text: str) -> str:
    text = LINK_RE.sub(r"\1", text)
    return " ".join(re.sub(r"[*_`#]", "", text).split()).rstrip(":")


def strip_markup(text: str) -> str:
    """Prose only: no comments, code, images, link targets, URLs, tags or
    template placeholders."""
    text = HTML_COMMENT_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" ", text)
    text = IMAGE_RE.sub(" ", text)
    text = LINK_RE.sub(r"\1", text)
    text = REF_DEF_RE.sub(" ", text)
    text = PLACEHOLDER_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    return HTML_TAG_RE.sub(" ", text)


def words(text: str) -> list:
    return WORD_RE.findall(strip_markup(text or ""))


def placeholders(text: str) -> list:
    """Template placeholders still in text: [bracketed instructions] of 20
    characters or more. "[Optional ...]" notes are allowed to stay."""
    found = []
    for line in HTML_COMMENT_RE.sub(" ", text or "").split("\n"):
        for match in PLACEHOLDER_RE.finditer(line):
            if not match.group(1).strip().lower().startswith("optional"):
                found.append(match.group(0))
    return found


def resolve(src: str, readme_dir: str, owner: str, repo: str):
    """The repo path an image or link points at, or None if it points
    outside the repo. Handles relative and /rooted paths, and blob, raw and
    raw.githubusercontent.com links to the same repo."""
    src = (src or "").strip()
    parts = urlsplit(src)
    if parts.scheme in ("http", "https"):
        host = parts.netloc.lower()
        segs = [unquote(s) for s in parts.path.split("/") if s]
        same = len(segs) >= 2 and segs[0].lower() == owner.lower() and segs[1].lower() == repo.lower()
        if host in ("github.com", "www.github.com") and same and len(segs) >= 5 \
                and segs[2] in ("blob", "raw"):
            path = "/".join(segs[4:])
        elif host == "raw.githubusercontent.com" and same and len(segs) >= 4:
            path = "/".join(segs[3:])
        else:
            return None
    elif parts.scheme or src.startswith("//") or not parts.path:
        return None
    else:
        path = unquote(parts.path)
        path = path.lstrip("/") if path.startswith("/") else posixpath.join(readme_dir, path)
    path = posixpath.normpath(path)
    return None if path in (".", "..") or path.startswith("../") else path


def _refs(doc: Doc) -> dict:
    return {m.group(1).strip().lower(): m.group(2) for m in REF_DEF_RE.finditer(doc.outside_code())}


def images(doc: Doc, readme_dir: str, owner: str, repo: str) -> list:
    """Repo paths of every image the README shows."""
    text, refs = HTML_COMMENT_RE.sub(" ", doc.outside_code()), _refs(doc)
    srcs = [m.group(2) for m in IMAGE_RE.finditer(text)]
    for m in IMAGE_REF_RE.finditer(text):
        label = (m.group(2) or m.group(1) or "").strip().lower()
        if label in refs:
            srcs.append(refs[label])
    srcs += [m.group(2) for m in HTML_IMG_RE.finditer(text)]
    return [p for p in (resolve(s, readme_dir, owner, repo) for s in srcs) if p]


def links(doc: Doc, readme_dir: str, owner: str, repo: str) -> list:
    """Repo paths of every link in the README (bare same-repo URLs too)."""
    text, refs = HTML_COMMENT_RE.sub(" ", doc.outside_code()), _refs(doc)
    targets = [m.group(2) for m in LINK_RE.finditer(text)]
    targets += [refs[m.group(2).strip().lower() or m.group(1).strip().lower()]
                for m in LINK_REF_RE.finditer(text)
                if (m.group(2).strip().lower() or m.group(1).strip().lower()) in refs]
    targets += [m.group(2) for m in HTML_LINK_RE.finditer(text)]
    targets += [m.group(0).strip("<>") for m in URL_RE.finditer(text)]
    return [p for p in (resolve(t, readme_dir, owner, repo) for t in targets) if p]


def has_run_instructions(doc: Doc) -> bool:
    return bool(COMMAND_RE.search(doc.code_text()))


def fingerprint(doc: Doc, ladder: bool) -> str:
    """A fingerprint of the member's own words (What I added and the
    portfolio paragraph for a ladder project, the whole README otherwise),
    so the same README can't earn two people a certificate."""
    if ladder:
        text = " ".join(doc.section(SECTIONS[k][0]) or "" for k in ("added", "portfolio"))
    else:
        text = doc.prose_lines()
    tokens = [w.lower() for w in words(text)]
    if len(tokens) < 20:
        return ""
    return hashlib.sha256(" ".join(tokens).encode("utf-8")).hexdigest()


# ── The README checks ───────────────────────────────────────────────────────
def check(project, doc: Doc, files: set, readme_path: str, owner: str, repo: str) -> list:
    """An Item for each thing the project's README must have."""
    readme_dir = posixpath.dirname(readme_path)
    items = []
    for need in project.readme:
        if need.kind == "charts":
            shown = images(doc, readme_dir, owner, repo)
            for chart in project.charts:
                items.append(_chart(chart, shown, files))
        elif need.kind == "run":
            items.append(Item(has_run_instructions(doc), "README has run instructions",
                              "" if has_run_instructions(doc) else
                              "no command that runs the project (python …, pip install …) in a "
                              "code block"))
        elif need.kind in SECTIONS:
            items.append(_section(doc, need.kind))
        elif need.kind == "notice":
            pattern, label = NOTICES[need.arg]
            ok = bool(re.search(pattern, doc.top(), re.I))
            items.append(Item(ok, f"README has {label}",
                              "" if ok else "put it above the first ## heading"))
        elif need.kind == "doesnt_show":
            ok = bool(re.search(r"does(?:n['’]t| not) show", doc.text, re.I))
            items.append(Item(ok, f"README has {need.label}"))
        elif need.kind == "passed":
            ok = bool(re.search(rf"\b{need.arg} passed\b", doc.text, re.I))
            items.append(Item(ok, f'README has the test command and its result ("{need.arg} passed")'))
        elif need.kind == "mention":
            ok = bool(re.search(need.arg, HTML_COMMENT_RE.sub(" ", doc.text), re.I))
            items.append(Item(ok, f"README {need.label}"))
        elif need.kind == "link":
            ok = need.arg in links(doc, readme_dir, owner, repo) and need.arg in files
            items.append(Item(ok, f"README {need.label}", "" if ok else
                              f"{need.arg} isn't in the repo" if need.arg not in files else
                              f"add a link to {need.arg}"))
        else:  # pragma: no cover - rules.py and this module disagree
            raise ValueError(f"unknown README need {need.kind!r}")
    if project.ladder:
        left = placeholders(doc.prose_lines())
        items.append(Item(not left, "README has no template placeholders left",
                          f"still there: {left[0][:80]}" + (f" (and {len(left) - 1} more)"
                                                            if len(left) > 1 else "")
                          if left else ""))
    return items


def chart_path(chart: str, shown: list, files: set):
    """The repo path of the chart the README shows, if it's in the repo."""
    return next((p for p in shown if posixpath.basename(p) == chart and p in files), None)


def _chart(chart: str, shown: list, files: set) -> Item:
    if chart_path(chart, shown, files):
        return Item(True, f"README shows {chart}")
    if not any(posixpath.basename(f) == chart for f in files):
        return Item(False, f"README shows {chart}", f"{chart} isn't in the repo")
    return Item(False, f"README shows {chart}",
                f"{chart} is in the repo but the README doesn't show it: add ![…]({chart})")


def _section(doc: Doc, kind: str) -> Item:
    pattern, label, minimum = SECTIONS[kind]
    text = doc.section(pattern)
    if text is None and kind == "where":
        found = re.search(pattern, doc.outside_code(), re.I)   # a bold lead-in, not a heading
        if found:
            rest = doc.outside_code()[found.end():]
            text = rest.split("\n\n")[0] if rest.strip() else ""
    what = f"README has the {label} section" if kind != "portfolio" else f"README has the {label}"
    if text is None:
        return Item(False, what, "not found")
    left = placeholders(text)
    if left:
        return Item(False, what, f"it still has the template's placeholder: {left[0][:80]}")
    count = len(words(text))
    if count < minimum:
        return Item(False, what, f"only {count} words of your own; write at least {minimum}")
    return Item(True, what)
