"""A small GitHub REST client on the standard library: just the calls the
checker, the report and the issuer make. A token is optional (public data
reads without one, at a lower rate limit).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
ACTIONS_BOT_ID = 41898282       # github-actions[bot], the author of workflow comments
MAX_PAGES = 10


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHub:
    def __init__(self, token: str | None = None, api: str = API, opener=None, sleep=time.sleep):
        self.token, self.api = token or None, api.rstrip("/")
        self.opener = opener or urllib.request.build_opener()
        self.sleep = sleep

    # ── Plumbing ────────────────────────────────────────────────────────────
    def request(self, method: str, path: str, body=None, params=None, attempts: int = 4):
        """(status, data, headers). Retries server errors and short rate-limit
        waits; raises GitHubError for anything else that isn't 2xx or 404."""
        url = path if path.startswith("https://") else self.api + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "qbc-checker",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with self.opener.open(req, timeout=30) as response:
                    raw = response.read(20_000_000)
                    return response.status, (json.loads(raw) if raw.strip() else None), \
                        dict(response.headers)
            except urllib.error.HTTPError as error:
                raw = error.read(200_000)
                wait = self._wait(error.code, error.headers)
                if wait is not None and attempt < attempts - 1:
                    self.sleep(wait)
                    continue
                if error.code == 404:
                    return 404, None, dict(error.headers or {})
                raise GitHubError(error.code, _message(raw)) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if attempt == attempts - 1:
                    raise GitHubError(0, f"can't reach GitHub ({error})") from None
                self.sleep(2 ** attempt)
        raise GitHubError(0, "gave up")  # pragma: no cover

    @staticmethod
    def _wait(status, headers):
        headers = headers or {}
        if status >= 500:
            return 3
        if status in (403, 429):
            if headers.get("Retry-After", "").isdigit():
                wait = int(headers["Retry-After"])
            elif headers.get("X-RateLimit-Remaining") == "0" and \
                    headers.get("X-RateLimit-Reset", "").isdigit():
                wait = int(headers["X-RateLimit-Reset"]) - int(time.time()) + 1
            else:
                return None
            return wait if 0 <= wait <= 90 else None
        return None

    def get(self, path: str, **params):
        """The JSON at path, or None for 404."""
        status, data, _ = self.request("GET", path, params=params or None)
        return None if status == 404 else data

    def paginate(self, path: str, limit: int = 1000, key: str | None = None, **params) -> list:
        items, page = [], 1
        while len(items) < limit and page <= MAX_PAGES:
            status, data, _ = self.request("GET", path, params={**params, "per_page": 100, "page": page})
            if status == 404 or data is None:
                break
            batch = data.get(key, []) if key else data
            items += batch
            if len(batch) < 100:
                break
            page += 1
        return items[:limit]

    # ── Member repos ────────────────────────────────────────────────────────
    def repo(self, owner: str, name: str):
        return self.get(f"/repos/{_q(owner)}/{_q(name)}")

    def public_member(self, org: str, login: str) -> bool:
        status, _, _ = self.request("GET", f"/orgs/{_q(org)}/public_members/{_q(login)}")
        return status == 204

    def commit_logins(self, owner: str, name: str, sha: str, limit: int = 500) -> dict:
        """{commit sha: (author login or None, committer login or None)}."""
        found = {}
        for c in self.paginate(f"/repos/{_q(owner)}/{_q(name)}/commits", limit=limit, sha=sha):
            found[c.get("sha")] = ((c.get("author") or {}).get("login"),
                                   (c.get("committer") or {}).get("login"))
        return found

    def activity(self, owner: str, name: str, limit: int = 1000) -> list:
        """The repo's pushes, oldest first, as GitHub logged them (its
        Activity tab): who pushed which commit to which branch, and when.
        GitHub sets that time, so unlike a commit's date nobody can change it."""
        path, params, found = f"/repos/{_q(owner)}/{_q(name)}/activity", \
            {"per_page": 100, "direction": "asc"}, []
        for _ in range(MAX_PAGES):
            status, data, headers = self.request("GET", path, params=params)
            if status == 404 or not isinstance(data, list):
                break
            found += data
            params = _next_page(headers)
            if not params or len(found) >= limit:
                break
        return found[:limit]

    def workflow_runs(self, owner: str, name: str, branch: str, limit: int = 30) -> list:
        return self.paginate(f"/repos/{_q(owner)}/{_q(name)}/actions/runs", limit=limit,
                             key="workflow_runs", branch=branch, exclude_pull_requests="true")

    def run_jobs(self, owner: str, name: str, run_id: int) -> list:
        return self.paginate(f"/repos/{_q(owner)}/{_q(name)}/actions/runs/{int(run_id)}/jobs",
                             limit=200, key="jobs")

    # ── Claim issues ────────────────────────────────────────────────────────
    def issue(self, repo: str, number: int):
        return self.get(f"/repos/{repo}/issues/{int(number)}")

    def issues(self, repo: str, labels: str, state: str = "open", limit: int = 300) -> list:
        found = self.paginate(f"/repos/{repo}/issues", limit=limit, labels=labels, state=state)
        return [i for i in found if "pull_request" not in i]

    def comments(self, repo: str, number: int) -> list:
        return self.paginate(f"/repos/{repo}/issues/{int(number)}/comments", limit=1000)

    def comment(self, repo: str, number: int, body: str) -> dict:
        return self.request("POST", f"/repos/{repo}/issues/{int(number)}/comments",
                            body={"body": body})[1]

    def ensure_label(self, repo: str, name: str, color: str, description: str) -> None:
        try:
            self.request("POST", f"/repos/{repo}/labels",
                         body={"name": name, "color": color, "description": description})
        except GitHubError as error:
            if error.status != 422:     # 422: it exists already
                raise

    def set_labels(self, repo: str, number: int, add=(), remove=()) -> None:
        for name in remove:
            self.request("DELETE", f"/repos/{repo}/issues/{int(number)}/labels/{_q(name)}")
        if add:
            self.request("POST", f"/repos/{repo}/issues/{int(number)}/labels",
                         body={"labels": list(add)})

    def close(self, repo: str, number: int, reason: str = "completed") -> None:
        self.request("PATCH", f"/repos/{repo}/issues/{int(number)}",
                     body={"state": "closed", "state_reason": reason})


def is_actions_bot(user) -> bool:
    """True for comments written by this repo's workflows (github-actions[bot])."""
    return isinstance(user, dict) and user.get("id") == ACTIONS_BOT_ID \
        and user.get("type") == "Bot" and user.get("login") == "github-actions[bot]"


def _q(part: str) -> str:
    return urllib.parse.quote(str(part), safe="")


def _next_page(headers: dict):
    """The query of the next page, from a cursor-paged Link header, or None."""
    link = next((v for k, v in (headers or {}).items() if k.lower() == "link"), "")
    for part in link.split(","):
        match = re.fullmatch(r'\s*<([^>]+)>\s*;\s*rel="next"\s*', part)
        if match:
            return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(match.group(1)).query)) or None
    return None


def _message(raw: bytes) -> str:
    try:
        return str(json.loads(raw).get("message", ""))[:200]
    except (ValueError, AttributeError):
        return raw[:200].decode("utf-8", "replace")
