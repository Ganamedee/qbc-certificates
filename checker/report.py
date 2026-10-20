"""The report job: posts the check job's result on the claim. It never runs
members' code, and it treats the result file as untrusted: the shape is
checked strictly, the claim it describes must be this issue, opened by this
account, with this exact body, and all text is escaped on the way into
Markdown (result.comment).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import claims, result
from .gh import GitHubError
from .sweep import LABELS


def post(gh, repo: str, event: dict, result_path: Path, check_job: str, run_url: str,
         forms_url: str, guide_url: str, site_url: str = "") -> str:
    issue = event.get("issue") or {}
    number = int(issue["number"])
    try:
        res = load_result(result_path, issue) if check_job == "success" else None
    except ValueError as error:
        print(f"Result refused: {error}")
        res = None
    if res == "skip":
        return "skipped"
    for name, (color, description) in LABELS.items():
        gh.ensure_label(repo, name, color, description)
    if res is None:
        gh.comment(repo, number, result.error_comment(run_url))
        return "error"
    gh.comment(repo, number, result.comment(res, forms_url=forms_url, guide_url=guide_url,
                                            claim_url=f"{site_url}/claim/{number}" if site_url else ""))
    labels = {l.get("name") for l in issue.get("labels") or []}
    if res.passed:
        add = ["confirmed"] if res.kind == "milestone" else ["passed"]
    else:
        add = ["failed"]
    gh.set_labels(repo, number, add=add,
                  remove=[n for n in ("passed", "failed", "refused", "waiting") if n in labels and n not in add])
    if res.passed and res.kind == "milestone":
        gh.close(repo, number, "completed")
    try:
        gh.request("PUT", f"/repos/{repo}/actions/workflows/sweep.yml/enable")
    except GitHubError as error:
        print(f"Couldn't turn the sweep back on: {error}")
    return "passed" if res.passed else "failed"


def load_result(path: Path, issue: dict):
    """The check job's result, if it describes exactly this claim."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and set(data) == {"skip"}:
        return "skip"
    res = result.load(data)
    user = issue.get("user") or {}
    if res.issue != issue.get("number") or res.login != user.get("login") \
            or res.user_id != user.get("id"):
        raise ValueError("the result is for another claim")
    if res.body_sha256 != claims.body_sha256(issue.get("body")):
        raise ValueError("the result is for another version of the claim")
    return res
