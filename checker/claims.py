"""A claim is a GitHub issue opened from one of the claim forms. This module
reads the form back out of the issue body and checks what was typed, with
the same rules qbc_cert.py applies to a certificate issued by hand.

GitHub writes a form into the issue body as one "### <label>" heading per
field, followed by the answer ("_No response_" when an optional one was
left empty, "- [X] <option>" for a ticked box).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from qbc_validate import QbcError, clean_name, clean_repo_url

from . import rules

FIELD_CLAIM = "What are you claiming?"
FIELD_NAME = "Your name, as it should appear on the certificate"
FIELD_PRIVACY = "Your certificate's page"
FIELD_CONFIRM = "Confirm"
OPTION_HIDE = "Don't list my repos on my certificate's page"
OPTION_OWN = "These repos are mine, and the work in them is my own"
NO_RESPONSE = "_No response_"
MAX_BODY = 20_000

HEADING_RE = re.compile(r"^###[ \t]+(.+?)[ \t]*$")
CHECKBOX_RE = re.compile(r"^[ \t]*[-*][ \t]+\[([ xX])\][ \t]+(.+?)[ \t]*$", re.M)
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")


@dataclass
class Request:
    """A claim, read and checked: who asked, for what, with which repos."""
    issue: int
    login: str
    user_id: int
    claim: rules.Claim | None
    body_sha256: str = ""
    name: str = ""
    repos: dict = field(default_factory=dict)   # slot key -> repo URL ("" if left empty)
    show_evidence: bool = True
    problems: list = field(default_factory=list)

    @property
    def evidence(self) -> list:
        return [url for url in self.repos.values() if url]


def body_sha256(body) -> str:
    """The fingerprint of an issue body, so the issuer can tell whether the
    claim was edited after it was checked."""
    text = (body or "").replace("\r\n", "\n").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_body(body: str) -> dict:
    """{label: answer} for every "### label" in a form's issue body. The
    first answer under a label counts; an empty one is ""."""
    fields, label, lines = {}, None, []

    def keep():
        if label is not None and label not in fields:
            answer = "\n".join(lines).strip()
            fields[label] = "" if answer == NO_RESPONSE else answer

    for line in (body or "").replace("\r\n", "\n").split("\n"):
        heading = HEADING_RE.match(line)
        if heading:
            keep()
            label, lines = heading.group(1), []
        elif label is not None:
            lines.append(line)
    keep()
    return fields


def ticked(answer: str) -> set:
    """The options ticked in a checkboxes answer."""
    return {text for mark, text in CHECKBOX_RE.findall(answer or "") if mark in "xX"}


def read_issue(issue: dict) -> Request:
    """A Request from a GitHub issue (an event payload's "issue", or the API's)."""
    user = issue.get("user") or {}
    login, user_id = user.get("login"), user.get("id")
    if not isinstance(login, str) or not LOGIN_RE.fullmatch(login) \
            or type(user_id) is not int or user.get("type") != "User":
        raise QbcError("This issue wasn't opened by a GitHub user account.")
    body = issue.get("body") or ""
    request = Request(issue=int(issue["number"]), login=login, user_id=user_id, claim=None,
                      body_sha256=body_sha256(body))
    if len(body) > MAX_BODY:
        request.problems.append("The issue is too long to be a claim form. Open a new claim "
                                "from the claim forms.")
        return request
    read_form(request, parse_body(body))
    return request


def read_form(request: Request, fields: dict) -> None:
    """Fill in request from a parsed form, noting every problem."""
    answer = fields.get(FIELD_CLAIM, "").strip()
    claim = rules.CLAIMS_BY_TITLE.get(answer.lower())
    if claim is None:
        request.problems.append(
            f'The "{FIELD_CLAIM}" answer is missing or isn\'t one of the claims. '
            "Open a new claim from the claim forms.")
        return
    request.claim = claim
    if claim.kind == "certificate":
        try:
            request.name = clean_name(fields.get(FIELD_NAME, ""))
        except QbcError as error:
            request.problems.append(f"Your name: {error}".replace(" (--name)", ""))
        request.show_evidence = OPTION_HIDE not in ticked(fields.get(FIELD_PRIVACY, ""))
    if OPTION_OWN not in ticked(fields.get(FIELD_CONFIRM, "")):
        request.problems.append(f'Tick "{OPTION_OWN}" under {FIELD_CONFIRM}.')
    read_repos(request, {slot.key: fields.get(slot.label, "") for slot in claim.slots})


def read_repos(request: Request, answers: dict) -> None:
    """Each slot's repo link, cleaned. The same repo can't fill two slots."""
    seen = {}
    for slot in request.claim.slots:
        raw = (answers.get(slot.key) or "").strip()
        if re.match(r"(?:www\.)?github\.com/", raw, re.I):
            raw = "https://" + raw      # pasted without the https://
        if not raw:
            if not slot.optional:
                request.problems.append(f"{slot.label}: no repo link.")
            request.repos[slot.key] = ""
            continue
        try:
            url = clean_repo_url(raw)
        except QbcError as error:
            request.problems.append(f"{slot.label}: {error}")
            request.repos[slot.key] = ""
            continue
        if url.lower() in seen:
            request.problems.append(f"{slot.label}: the same repo as {seen[url.lower()]}. "
                                    "Each project needs its own repo.")
        seen.setdefault(url.lower(), slot.label)
        request.repos[slot.key] = url


def from_args(claim_id: str, login: str, repos: list, name: str = "", user_id: int = 0,
              issue: int = 0, show_evidence: bool = True) -> Request:
    """A Request typed on the command line (local runs): repos in slot order."""
    if claim_id not in rules.CLAIMS_BY_ID:
        raise QbcError(f"No claim {claim_id!r}. Claims: {', '.join(rules.CLAIMS_BY_ID)}")
    if not LOGIN_RE.fullmatch(login or ""):
        raise QbcError(f"Not a GitHub username: {login!r}")
    claim = rules.claim(claim_id)
    if len(repos) > len(claim.slots):
        raise QbcError(f"{claim.title} takes at most {len(claim.slots)} repos.")
    request = Request(issue=issue, login=login, user_id=user_id, claim=claim,
                      show_evidence=show_evidence)
    if claim.kind == "certificate":
        try:
            request.name = clean_name(name)
        except QbcError as error:
            request.problems.append(f"Your name: {error}")
    read_repos(request, {slot.key: url for slot, url in zip(claim.slots, repos)})
    return request
