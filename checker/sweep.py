"""The sweep, which runs in the public claim forms repo every hour: for each
passed certificate claim it reads what the issuer did (the club site's
/claims/<issue>.json), tells the member on the issue, and closes it once
the certificate is live. It reads the site like anyone would, so the claims
repo needs no access to the private repo.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request

from .gh import is_actions_bot
from .result import md

CODE_RE = re.compile(r"QBC-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}")
LINKEDIN = "https://www.linkedin.com/profile/add?"
LABELS = {
    "passed": ("0e8a16", "All checks passed; the certificate is on its way"),
    "failed": ("d93f0b", "Some checks failed"),
    "issued": ("5319e7", "Certificate issued"),
    "confirmed": ("0e8a16", "Milestone confirmed"),
    "waiting": ("fbca04", "Waiting on something outside the checks"),
    "refused": ("b60205", "Passed the checks but can't be issued as it is"),
}


def fetch_json(url: str, opener=None):
    """JSON from the club site, or None if there's nothing there (yet)."""
    request = urllib.request.Request(url, headers={"User-Agent": "qbc-checker"})
    try:
        with (opener or urllib.request.build_opener()).open(request, timeout=20) as response:
            return json.loads(response.read(100_000))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    except ValueError:
        return None


def valid_status(status, issue: dict, base_url: str) -> bool:
    if not isinstance(status, dict) or status.get("issue") != issue.get("number") \
            or status.get("user_id") != (issue.get("user") or {}).get("id"):
        return False
    if status.get("status") == "issued":
        code = status.get("code")
        return isinstance(code, str) and bool(CODE_RE.fullmatch(code)) \
            and status.get("verify_url") == f"{base_url}/verify/{code}" \
            and isinstance(status.get("linkedin_url"), str) \
            and status["linkedin_url"].startswith(LINKEDIN)
    return status.get("status") in ("waiting", "refused") and isinstance(status.get("reason"), str)


def marker(status: dict) -> str:
    if status["status"] == "issued":
        return f"<!-- qbc-status issued {status['code']} -->"
    digest = hashlib.sha256(status["reason"].encode()).hexdigest()[:16]
    return f"<!-- qbc-status {status['status']} {digest} -->"


def message(status: dict, title: str) -> str:
    if status["status"] == "issued":
        url = status["verify_url"]
        files = (f"- The certificate itself: [the image]({url}/certificate.png) to post, and "
                 f"[the PDF]({url}/certificate.pdf) to print\n") if status.get("image") is True else ""
        return (f"## 🎓 Your certificate is live\n\n**{md(title, 120)}**, code `{status['code']}`\n\n"
                f"- Its page, which anyone can open to check it: {url}\n"
                f"- [Add it to your LinkedIn profile]({status['linkedin_url']})\n" + files + "\n"
                "The page always shows whether the certificate is valid, so share the link "
                "wherever you like. Congratulations!\n\n" + marker(status) + "\n")
    if status["status"] == "waiting":
        return f"## ⏳ Not issued yet\n\n{md(status['reason'])}\n\n" + marker(status) + "\n"
    return (f"## ⚠️ Passed the checks, but not issued\n\n{md(status['reason'])}\n\n"
            + marker(status) + "\n")


def sweep(gh, repo: str, base_url: str, fetch=fetch_json) -> list:
    """One pass over the open passed claims. Returns what it did, a line each."""
    from .claims import parse_body, FIELD_CLAIM
    done = []
    for name, (color, description) in LABELS.items():
        gh.ensure_label(repo, name, color, description)
    for issue in gh.issues(repo, labels="passed", state="open"):
        number = issue["number"]
        status = fetch(f"{base_url}/claims/{number}.json")
        if status is None:
            continue
        if not valid_status(status, issue, base_url):
            done.append(f"#{number}: ignored an unexpected status file")
            continue
        comments = gh.comments(repo, number)
        said = any(is_actions_bot(c.get("user")) and marker(status) in (c.get("body") or "")
                   for c in comments)
        title = parse_body(issue.get("body") or "").get(FIELD_CLAIM) or "your certificate"
        if not said:
            gh.comment(repo, number, message(status, title))
        if status["status"] == "issued":
            gh.set_labels(repo, number, add=["issued"],
                          remove=[n for n in ("passed", "waiting") if _has(issue, n)])
            gh.close(repo, number, "completed")
        elif status["status"] == "waiting" and not _has(issue, "waiting"):
            gh.set_labels(repo, number, add=["waiting"])
        elif status["status"] == "refused":
            gh.set_labels(repo, number, add=["refused"],
                          remove=[n for n in ("passed", "waiting") if _has(issue, n)])
        done.append(f"#{number}: {status['status']}")
    return done


def _has(issue: dict, label: str) -> bool:
    return any(l.get("name") == label for l in issue.get("labels") or [])
