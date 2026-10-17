"""Checks for what people type: names, emails, notes and GitHub repo links.
Shared by qbc_cert.py (the owner's tool) and qbc_check.py (the automatic
checker), so a claim is held to exactly the rules a manual issue is.
Python 3.9+, standard library only.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

EMAIL_RE = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
GITHUB_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
GITHUB_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
# First path segments of github.com that are pages, not accounts.
GITHUB_RESERVED = frozenset((
    "about", "apps", "collections", "contact", "customer-stories", "enterprise",
    "events", "explore", "features", "issues", "join", "login", "marketplace",
    "new", "notifications", "orgs", "organizations", "pricing", "pulls",
    "search", "security", "settings", "site", "sponsors", "topics",
    "trending", "users"))
# Characters that reorder text: a name could use them to display as another.
BIDI_CONTROLS = frozenset("؜‎‏‪‫‬‭‮"
                          "⁦⁧⁨⁩")
MAX_NAME = 100
MAX_TEXT = 500
MAX_URL = 300


class QbcError(Exception):
    """Input or state the command refuses; the CLI prints it and exits 1."""


def _shown(value) -> str:
    """User input as it may appear in an error message: escaped, short."""
    return ascii(str(value)[:80])


def clean_name(value) -> str:
    """The name to print: NFC, single spaces, no control or bidi characters."""
    if not isinstance(value, str) or not value.strip():
        raise QbcError("A name is required (--name).")
    text = unicodedata.normalize("NFC", value)
    for ch in text:
        joiner = ch in "‌‍"  # ZWNJ and ZWJ: needed by some scripts
        if ch in BIDI_CONTROLS or (not joiner and unicodedata.category(ch)
                                   in ("Cc", "Cf", "Cs", "Co", "Zl", "Zp")):
            raise QbcError(f"The name contains a control character ({ascii(ch)}). "
                           "Type it again as plain text.")
    text = " ".join(text.split())
    if len(text) > MAX_NAME:
        raise QbcError(f"The name is longer than {MAX_NAME} characters.")
    if not any(unicodedata.category(ch).startswith("L") for ch in text):
        raise QbcError(f"The name has no letters: {_shown(text)}")
    return text


def clean_text(value, what: str) -> str:
    """A private note or reason: control characters become spaces."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise QbcError(f"The {what} must be text.")
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Zl", "Zp")
                   or ch in BIDI_CONTROLS else ch for ch in value)
    text = " ".join(text.split())
    if len(text) > MAX_TEXT:
        raise QbcError(f"The {what} is longer than {MAX_TEXT} characters.")
    return text


def clean_email(value) -> str:
    """The member's identity: trimmed and lower-cased."""
    if not isinstance(value, str):
        raise QbcError("An email address is required (--email).")
    email = value.strip().lower()
    local = email.split("@")[0]
    if (len(email) > 254 or not EMAIL_RE.fullmatch(email) or ".." in email
            or local.startswith(".") or local.endswith(".")):
        raise QbcError(f"Not a valid email address: {_shown(value)}")
    return email


def clean_repo_url(value) -> str:
    """A public GitHub repo as https://github.com/<owner>/<repo>. Deeper links
    (…/tree/main) are reduced to the repo; anything else is refused."""
    if not isinstance(value, str):
        raise QbcError("Evidence must be a GitHub repo URL.")
    raw = value.strip()
    if not raw or len(raw) > MAX_URL or not raw.isascii() \
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise QbcError(f"Not a usable URL: {_shown(raw)}")
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise QbcError(f"Evidence must be an https:// GitHub repo link: {_shown(raw)}")
    if parts.netloc.lower() not in ("github.com", "www.github.com"):
        raise QbcError(f"Evidence must be a repo on github.com: {_shown(raw)}")
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        raise QbcError("Not a repo link (expected https://github.com/<owner>/<repo>): "
                       f"{_shown(raw)}")
    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if (not GITHUB_OWNER_RE.fullmatch(owner) or owner.lower() in GITHUB_RESERVED
            or not GITHUB_REPO_RE.fullmatch(repo) or set(repo) == {"."}):
        raise QbcError(f"Not a GitHub repo link: {_shown(raw)}")
    return f"https://github.com/{owner}/{repo}"


def merge_evidence(*lists) -> list:
    """Repo links in order, each repo once (GitHub ignores case)."""
    seen, out = set(), []
    for url in (u for urls in lists for u in urls):
        if url.lower() not in seen:
            seen.add(url.lower())
            out.append(url)
    return out
