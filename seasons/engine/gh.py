"""The few GitHub REST calls the seasons workflow makes, with the standard library only.

The workflow's own token (GITHUB_TOKEN) is enough: the score job reads issues, and the post job comments, labels
and closes them in the repo it runs in.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo: str, token: str | None = None, opener=None, pause=time.sleep):
        self.repo = repo
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.opener = opener or urllib.request.build_opener()
        self.pause = pause

    def _call(self, method: str, path: str, body=None, query: dict | None = None):
        url = f"{API}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "qbc-seasons"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            request = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with self.opener.open(request, timeout=30) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as error:
                if error.code in (500, 502, 503, 504) and attempt < 2:
                    self.pause(2 * (attempt + 1))
                    continue
                wait = (error.headers or {}).get("Retry-After") if error.code in (403, 429) else None
                if wait and wait.isdigit() and int(wait) <= 120 and attempt < 2:   # a rate limit: wait it out once
                    self.pause(int(wait) + 1)
                    continue
                raise GitHubError(f"{method} {path}: HTTP {error.code}") from None
            except urllib.error.URLError as error:
                if attempt < 2:
                    self.pause(2 * (attempt + 1))
                    continue
                raise GitHubError(f"{method} {path}: {error.reason}") from None
        raise GitHubError(f"{method} {path}: no answer")

    def _pages(self, path: str, query: dict, limit: int = 50) -> list:
        found = []
        for page in range(1, limit + 1):
            items = self._call("GET", path, query={**query, "per_page": 100, "page": page}) or []
            found += items
            if len(items) < 100:
                return found
        raise GitHubError(f"GET {path}: more than {limit} pages")

    def issues(self, label: str | None = None, since: str | None = None, state: str = "all") -> list:
        """Issues, oldest first, optionally only open ones, with a label or updated since a time. Pull requests
        are left out."""
        query = {"state": state, "sort": "created", "direction": "asc"}
        if label:
            query["labels"] = label
        if since:
            query["since"] = since
        return [i for i in self._pages(f"/repos/{self.repo}/issues", query) if "pull_request" not in i]

    def issue(self, number: int) -> dict:
        return self._call("GET", f"/repos/{self.repo}/issues/{int(number)}")

    def edited_times(self, numbers: list) -> dict:
        """{issue number: when its author last edited it, or None if never}, from the GraphQL API. Labels,
        comments and closing don't count as edits, so this is when the entry in the body was last changed."""
        owner, name = self.repo.split("/", 1)
        found = {}
        numbers = sorted({int(n) for n in numbers})
        for start in range(0, len(numbers), 50):
            chunk = numbers[start:start + 50]
            fields = " ".join(f"i{n}: issue(number: {n}) {{ lastEditedAt }}" for n in chunk)
            query = ("query($owner: String!, $name: String!) "
                     f"{{ repository(owner: $owner, name: $name) {{ {fields} }} }}")
            data = self._call("POST", "/graphql", {"query": query, "variables": {"owner": owner, "name": name}})
            repo = ((data or {}).get("data") or {}).get("repository") or {}
            for n in chunk:
                if isinstance(repo.get(f"i{n}"), dict):
                    found[n] = repo[f"i{n}"].get("lastEditedAt")
        return found

    def comments(self, number: int) -> list:
        return self._pages(f"/repos/{self.repo}/issues/{int(number)}/comments", {})

    def comment(self, number: int, body: str) -> dict:
        return self._call("POST", f"/repos/{self.repo}/issues/{int(number)}/comments", {"body": body}) or {}

    def edit_comment(self, comment_id: int, body: str) -> None:
        self._call("PATCH", f"/repos/{self.repo}/issues/comments/{int(comment_id)}", {"body": body})

    def set_state(self, number: int, state: str, reason: str | None = None) -> None:
        body = {"state": state}
        if reason:
            body["state_reason"] = reason
        self._call("PATCH", f"/repos/{self.repo}/issues/{int(number)}", body)

    def add_labels(self, number: int, labels: list) -> None:
        self._call("POST", f"/repos/{self.repo}/issues/{int(number)}/labels", {"labels": labels})

    def remove_label(self, number: int, name: str) -> None:
        try:
            self._call("DELETE", f"/repos/{self.repo}/issues/{int(number)}/labels/{urllib.parse.quote(name, safe='')}")
        except GitHubError as error:
            if "HTTP 404" not in str(error):   # 404: it isn't on the issue
                raise

    def ensure_label(self, name: str, colour: str, description: str) -> None:
        try:
            self._call("POST", f"/repos/{self.repo}/labels",
                       {"name": name, "color": colour, "description": description})
        except GitHubError as error:
            if "HTTP 422" not in str(error):   # 422: the label is already there
                raise
