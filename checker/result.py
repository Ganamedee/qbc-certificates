"""What a check found, in four forms: the result file the check job hands
to the report job, the comment the member reads, the result block hidden in
that comment, which the issuer reads back before issuing anything, and the
report block beside it, which the club site's claim page reads to show the
same answer.

The check job runs members' code, so the report job treats its result file
as untrusted: load() accepts only the exact shape below, and every piece of
text is escaped when it becomes Markdown.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

VERSION = 1
BLOCK_RE = re.compile(r"<!-- qbc-result v1 ([A-Za-z0-9+/=]{1,20000}) -->")
REPORT_RE = re.compile(r"<!-- qbc-report v1 ([A-Za-z0-9+/=]{1,60000}) -->")
MAX_COMMENT = 60_000    # GitHub refuses comments over 65,536 characters
MAX_REPOS = 10
MAX_ITEMS = 60
MAX_TEXT = 300
MAX_DETAIL = 600
MAX_LIST = 20


@dataclass
class Item:
    """One line of the report: something checked, and whether it passed."""
    ok: bool
    text: str
    detail: str = ""


@dataclass
class RepoResult:
    slot: str
    label: str
    url: str = ""
    project: str = ""       # rules.PROJECTS key, once known
    items: list = field(default_factory=list)
    skipped: bool = False   # an optional repo left empty
    readme_sha256: str = ""  # fingerprint of the member's own README text

    @property
    def ok(self) -> bool:
        return self.skipped or (bool(self.items) and all(i.ok for i in self.items))


@dataclass
class Result:
    issue: int
    login: str
    user_id: int
    claim: str
    kind: str               # "certificate" or "milestone"
    title: str
    name: str = ""
    evidence: list = field(default_factory=list)
    show_evidence: bool = True
    body_sha256: str = ""
    checked_at: str = ""
    rules: str = ""
    problems: list = field(default_factory=list)   # the form itself could not be used
    notes: list = field(default_factory=list)
    repos: list = field(default_factory=list)
    v: int = VERSION

    @property
    def passed(self) -> bool:
        return not self.problems and bool(self.repos) and all(r.ok for r in self.repos)

    def to_json(self) -> dict:
        data = asdict(self)
        data["passed"] = self.passed
        return data

    def block(self) -> str:
        """The hidden result block: what the issuer needs, nothing else."""
        payload = {
            "v": self.v, "issue": self.issue, "login": self.login, "user_id": self.user_id,
            "claim": self.claim, "kind": self.kind, "name": self.name,
            "evidence": self.evidence, "show_evidence": self.show_evidence,
            "body_sha256": self.body_sha256, "passed": self.passed,
            "checked_at": self.checked_at, "rules": self.rules,
            "fingerprints": [r.readme_sha256 for r in self.repos if r.readme_sha256],
        }
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        return "<!-- qbc-result v1 %s -->" % base64.b64encode(raw.encode()).decode()

    def report_block(self, details: bool = True) -> str:
        """The hidden report block: the comment's lines as data, for the claim
        page on the club site (qbc_claim.js), which shows them as plain text.
        Base64, like the result block, so nothing in it is read as Markdown."""
        payload = {
            "v": self.v, "passed": self.passed, "kind": self.kind, "title": self.title,
            "checked_at": self.checked_at, "problems": self.problems, "notes": self.notes,
            "repos": [{"label": r.label, "url": r.url, "skipped": r.skipped,
                       "items": [{"ok": i.ok, "text": i.text, "detail": i.detail if details else ""}
                                 for i in r.items]}
                      for r in self.repos],
        }
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        return "<!-- qbc-report v1 %s -->" % base64.b64encode(raw.encode()).decode()


# ── Reading a result back ───────────────────────────────────────────────────
def _str(value, limit, what, pattern=None) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{what}: not text, or too long")
    if pattern and not re.fullmatch(pattern, value):
        raise ValueError(f"{what}: unexpected value")
    return value


def _int(value, what) -> int:
    if type(value) is not int or not 0 <= value < 2 ** 53:
        raise ValueError(f"{what}: not a number")
    return value


def _bool(value, what) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{what}: not true or false")
    return value


def _list(value, what, limit=MAX_LIST) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{what}: not a list, or too long")
    return value


def _keys(data, required, what) -> None:
    if not isinstance(data, dict) or set(data) != set(required):
        raise ValueError(f"{what}: unexpected fields")


SHA_RE = r"(?:[0-9a-f]{64})?"
URL_RE = r"https://github\.com/[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}"
ITEM_KEYS = ("ok", "text", "detail")
REPO_KEYS = ("slot", "label", "url", "project", "items", "skipped", "readme_sha256")
RESULT_KEYS = ("issue", "login", "user_id", "claim", "kind", "title", "name", "evidence",
               "show_evidence", "body_sha256", "checked_at", "rules", "problems", "notes",
               "repos", "v", "passed")


def load(data) -> Result:
    """A result file from the check job, refused unless it has exactly the
    expected shape. The issuer applies the same rules to the result block."""
    _keys(data, RESULT_KEYS, "result")
    if data["v"] != VERSION:
        raise ValueError("result: unknown version")
    repos = []
    for raw in _list(data["repos"], "repos", MAX_REPOS):
        _keys(raw, REPO_KEYS, "repo")
        items = []
        for item in _list(raw["items"], "items", MAX_ITEMS):
            _keys(item, ITEM_KEYS, "item")
            items.append(Item(_bool(item["ok"], "ok"), _str(item["text"], MAX_TEXT, "text"),
                              _str(item["detail"], MAX_DETAIL, "detail")))
        url = _str(raw["url"], 200, "url")
        if url:
            _str(url, 200, "url", URL_RE)
        repos.append(RepoResult(
            slot=_str(raw["slot"], 20, "slot", r"[a-z0-9_]+"), label=_str(raw["label"], 120, "label"),
            url=url, project=_str(raw["project"], 20, "project", r"[a-z0-9_]*"), items=items,
            skipped=_bool(raw["skipped"], "skipped"),
            readme_sha256=_str(raw["readme_sha256"], 64, "readme_sha256", SHA_RE)))
    result = Result(
        issue=_int(data["issue"], "issue"), login=_str(data["login"], 39, "login", r"[A-Za-z0-9-]+"),
        user_id=_int(data["user_id"], "user_id"),
        claim=_str(data["claim"], 40, "claim", r"[a-z0-9_]*"),
        kind=_str(data["kind"], 20, "kind", r"certificate|milestone|"),
        title=_str(data["title"], 120, "title"), name=_str(data["name"], 120, "name"),
        evidence=[_str(u, 200, "evidence", URL_RE) for u in _list(data["evidence"], "evidence")],
        show_evidence=_bool(data["show_evidence"], "show_evidence"),
        body_sha256=_str(data["body_sha256"], 64, "body_sha256", SHA_RE),
        checked_at=_str(data["checked_at"], 40, "checked_at"),
        rules=_str(data["rules"], 40, "rules", r"[0-9a-f]*"),
        problems=[_str(p, MAX_DETAIL, "problem") for p in _list(data["problems"], "problems")],
        notes=[_str(n, MAX_DETAIL, "note") for n in _list(data["notes"], "notes")],
        repos=repos)
    if _bool(data["passed"], "passed") != result.passed:
        raise ValueError("result: passed does not match its items")
    return result


def read_block(text: str):
    """The payload of the last result block in a comment, or None."""
    found = BLOCK_RE.findall(text or "")
    if not found:
        return None
    try:
        payload = json.loads(base64.b64decode(found[-1], validate=True))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("v") == VERSION else None


# ── The comment ─────────────────────────────────────────────────────────────
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#!|~])")


def md(value, limit=MAX_DETAIL) -> str:
    """Text from a check, safe to put in Markdown: one line, short, every
    Markdown character escaped, no @mentions and no live links."""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(value)).split())
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    text = _MD_SPECIAL.sub(r"\\\1", text)
    return text.replace("@", "@⁠").replace("://", ":/⁠/").replace("www.", "www⁠.")


def _when(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return md(stamp, 40)
    return moment.strftime("%d %b %Y, %H:%M UTC").lstrip("0")


def _repo_name(url: str) -> str:
    return "/".join(url.split("/")[3:5])


def comment(result: Result, *, forms_url: str, guide_url: str, claim_url: str = "") -> str:
    """The comment the report job posts on the claim. claim_url is the
    claim's page on the club site, where members who claimed there see it."""
    title = md(result.title, 120)
    lines = []
    if result.passed:
        lines.append(f"## ✅ Passed: {title}")
    else:
        lines.append(f"## ❌ Not passed yet: {title}")
    lines.append("")
    if result.problems:
        lines.append("The checker couldn't use this claim:")
        lines.append("")
        lines += [f"- ❌ {md(p)}" for p in result.problems]
        lines.append("")
    for repo in result.repos:
        lines.append(f"### {md(repo.label, 120)}")
        if repo.url:
            lines.append(f"[{md(_repo_name(repo.url), 140)}]({repo.url})")
        lines.append("")
        if repo.skipped:
            lines.append("- ➖ Left empty, which is fine for this one.")
        for item in repo.items:
            mark = "✅" if item.ok else "❌"
            text = md(item.text, MAX_TEXT)
            lines.append(f"- {mark} {text}" + (f": {md(item.detail)}" if item.detail else ""))
        lines.append("")
    for note in result.notes:
        lines.append(f"> {md(note)}")
        lines.append("")
    lines.append(_next_steps(result, forms_url))
    lines.append("")
    site = f" · [See it on {claim_url.split('//')[-1].split('/')[0]}]({claim_url})" if claim_url else ""
    lines.append(f"<sub>Checked {_when(result.checked_at)}, checker version {md(result.rules or '?', 40)}. "
                 f"[What the checker looks at]({guide_url}){site}</sub>")
    lines.append("")
    lines.append(result.block())
    text = "\n".join(lines) + "\n"
    # The report block only repeats the comment, so it gives way first if the comment is long.
    for details in (True, False):
        report = result.report_block(details)
        if len(text) + len(report) + 1 <= MAX_COMMENT:
            return text + report + "\n"
    return text


def _next_steps(result: Result, forms_url: str) -> str:
    if result.passed and result.kind == "certificate":
        return ("**What happens next:** nothing for you to do. Your certificate is issued "
                "automatically within a few hours: a comment with its verify link and a LinkedIn "
                "button lands here, and this issue closes.")
    if result.passed:
        return (f"**Confirmed: {md(result.title, 120)}.** This issue is labelled `confirmed` and "
                "closes now: that's the club's record of your milestone.")
    if result.problems and not result.repos:
        return ("**What to do:** edit this issue to fix the form (the checks run again when you "
                f"save it), or open a new claim from [the claim forms]({forms_url}).")
    return ("**What to do:** fix every ❌ above and push the changes to GitHub, then comment "
            "`/recheck` on this issue and the checks run again. Editing the form runs them again too.")


def error_comment(run_url: str) -> str:
    """When the checker itself failed: the member did nothing wrong. The claim page finds this comment by its
    first line (ERROR_HEAD in qbc_claim.js), so that line never changes."""
    return ("## ⚠️ The checker hit a problem on its side\n\n"
            "This isn't about your repos. Comment `/recheck` to try again. If it happens twice, "
            f"the club owner can see what went wrong in [the run log]({run_url}).\n")
