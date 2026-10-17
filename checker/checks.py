"""Checking a claim, repo by repo, in the order the certificates guide lists:
public and the member's own, the starter pack's private files left out, a
commit history of their own, the README and its charts, then the tests.
Pathway build cards continue in pathway.py.
"""

from __future__ import annotations

import posixpath
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import files as filecheck
from . import pathway, readme, rules, trusted
from .gh import GitHubError
from .items import scan_item, tests_item
from .repo import MAX_REPO_KB, RepoError, clone
from .result import Item, RepoResult, Result
from .sandbox import SandboxError

@dataclass
class Context:
    gh: object                      # gh.GitHub
    sandbox: object                 # sandbox.Sandbox
    rules_version: str = ""
    club_accounts: frozenset = frozenset()   # logins whose commits are the club's starter packs
    now: object = field(default=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def trusted(self) -> Path:
        return Path(self.sandbox.opt) / "trusted"


def check_claim(request, ctx: Context) -> Result:
    claim = request.claim
    result = Result(issue=request.issue, login=request.login, user_id=request.user_id,
                    claim=claim.id if claim else "", kind=claim.kind if claim else "",
                    title=claim.title if claim else "your claim", name=request.name,
                    show_evidence=request.show_evidence, body_sha256=request.body_sha256,
                    checked_at=ctx.now(), rules=ctx.rules_version,
                    problems=list(request.problems))
    if claim is None:
        return result
    seen = {}
    for slot in claim.slots:
        url = request.repos.get(slot.key, "")
        if not url:
            if slot.optional:
                result.repos.append(RepoResult(slot=slot.key, label=slot.label, skipped=True))
            continue
        out = RepoResult(slot=slot.key, label=slot.label, url=url,
                         project=slot.project if slot.project in rules.PROJECTS else "")
        try:
            check_repo(ctx, request, slot, out, seen)
        except GitHubError as error:
            out.items.append(Item(False, "GitHub answered the checker",
                                  f"{error}. This isn't about your repo: comment /recheck later"))
        except (RepoError, SandboxError, OSError) as error:
            traceback.print_exc()
            out.items.append(Item(False, "The checker could run its checks",
                                  f"{error}. This may not be about your repo: comment /recheck to "
                                  "try again"))
        finally:
            ctx.sandbox.cleanup()
        result.repos.append(out)
    result.evidence = [r.url for r in result.repos if r.url and not r.skipped]
    return result


def check_repo(ctx: Context, request, slot, out: RepoResult, seen: dict) -> None:
    claim, items = request.claim, out.items
    owner, name = out.url.split("/")[3:5]
    info = ctx.gh.repo(owner, name)
    if info is None or info.get("private"):
        items.append(Item(False, "Public on GitHub",
                          "not found: check the link, and that the repo is public"))
        return
    out.url = f"https://github.com/{info['full_name']}"
    owner, name = info["full_name"].split("/")
    items.append(owner_item(ctx, info, request.login))
    if not claim.roster:
        items.append(Item(not info.get("fork"), "Your own repo, not a fork", "" if not info.get("fork")
                          else "make a new repository of your own and push your work there"))
    size = int(info.get("size") or 0)
    if size > MAX_REPO_KB:
        items.append(Item(False, "Small enough to check", f"{size // 1024} MB; the limit is "
                                                          f"{MAX_REPO_KB // 1024} MB"))
    if not all(i.ok for i in items):
        return
    # GitHub reports a size of 0 for a repo pushed minutes ago, so only the clone can say it's empty.
    try:
        repo = clone(owner, name, info.get("default_branch") or "main", ctx.sandbox.work_dir(slot.key))
    except RepoError:
        if size:
            raise
        repo = None
    if repo is None or not repo.files:
        items.append(Item(False, "Has files", "the repo is empty"))
        return
    if slot.project == "fin4":
        items += pathway.memo_items(ctx, repo, in_fin2=False)
        return

    if slot.project == "ladder":
        found = [p for p in rules.LADDER if f"starter/{p.module}" in repo.files]
        if not found:
            items.append(Item(False, "One of Projects 01 to 10", "no starter/ code from the club's "
                                                                   "projects (for example "
                                                                   "starter/quantum_coin.py)"))
            return
        out.project = found[0].key
        if out.project in seen:
            items.append(Item(False, f"A different project ({found[0].title})",
                              f"already used for {seen[out.project]}"))
            return
        items.append(Item(True, f"Recognised as {found[0].title}"))
    seen.setdefault(out.project, slot.label)
    project = rules.PROJECTS.get(out.project)

    if not claim.roster and (project is None or project.ladder):   # a sprint project is all theirs
        items.append(starter_files_item(repo))
    if claim.roster:
        items.append(Item(bool(repo.find("README.md", "README.markdown", "README.rst", "README.txt",
                                         "README")), "Has a README"))
    if project is None:
        return
    if project.ladder:
        module = f"starter/{project.module}"
        items.append(Item(module in repo.files, f"Your code is in {module}",
                          "" if module in repo.files else
                          "the club's tests run the code in starter/, as the starter pack lays it out"))
        if module not in repo.files:
            return
    if claim.history:
        items += history_items(ctx, repo, project, request.login)
    if claim.readme:
        items += readme_items(repo, project, out)
    if claim.pinned_requirements and project.key in ("01", "02", "03"):
        items.append(pinned_item(repo))
    if project.ladder:
        items += ladder_tests(ctx, repo, project)
    else:
        items += pathway.check_card(ctx, repo, project, request)


def owner_item(ctx: Context, info: dict, login: str) -> Item:
    owner = info.get("owner") or {}
    who = owner.get("login", "")
    if who.lower() == login.lower():
        return Item(True, "Public, and yours")
    if owner.get("type") == "Organization":
        if ctx.gh.public_member(who, login):
            return Item(True, f"Public, and in {who}, an organisation you're a public member of")
        return Item(False, "Public, and yours",
                    f"it belongs to the organisation {who}: make your membership of {who} public "
                    "(GitHub, Your organizations) or move the repo to your own account")
    return Item(False, "Public, and yours", f"it belongs to {who}, not to {login}, the account "
                                            "that opened this claim")


def starter_files_item(repo) -> Item:
    found = [p for p in sorted(repo.files) if p.lower() == "guide.md" or p.startswith("solution/")]
    if not found:
        return Item(True, "No GUIDE.md or solution/ folder")
    what = "GUIDE.md" if found[0].lower() == "guide.md" else "solution/"
    return Item(False, "No GUIDE.md or solution/ folder",
                f"found {what}: delete it from the repo (the starter pack's .gitignore keeps it out)")


def history_items(ctx: Context, repo, project, login: str) -> list:
    """Several commits of the member's own, some of them to the code, and
    none to the code by anyone else (the club's starter-pack commits and
    bots aside)."""
    if project.ladder:
        where, is_code = " in starter/", lambda f: f.startswith("starter/") and f.endswith(".py")
    else:
        where, is_code = "", lambda f: f.endswith(".py")
    authors = ctx.gh.commit_logins(repo.owner, repo.name, repo.branch)
    own, foreign = [], []
    for commit in repo.history():
        author = (authors.get(commit.sha) or (None, None))[0] or ""
        if author.lower().endswith("[bot]") or (author.lower() in ctx.club_accounts
                                                and author.lower() != login.lower()):
            continue
        if author and author.lower() != login.lower():
            if any(is_code(f) for f in commit.files):
                foreign.append((commit.sha[:7], author))
            continue
        own.append(commit)
    own_code = [c for c in own if any(is_code(f) for f in c.files)]
    return [
        Item(len(own) >= rules.MIN_COMMITS, f"At least {rules.MIN_COMMITS} commits of your own",
             "" if len(own) >= rules.MIN_COMMITS else
             f"{len(own)} found: build the project up in several commits, not one upload"),
        Item(len(own_code) >= rules.MIN_CODE_COMMITS,
             f"At least {rules.MIN_CODE_COMMITS} of them change your code{where}",
             "" if len(own_code) >= rules.MIN_CODE_COMMITS else f"{len(own_code)} found"),
        Item(not foreign, "Nobody else committed to the code",
             "" if not foreign else f"commit {foreign[0][0]} is by {foreign[0][1]}"
             + (f" (and {len(foreign) - 1} more)" if len(foreign) > 1 else "")),
    ]


def readme_items(repo, project, out: RepoResult) -> list:
    path = repo.find("README.md", "README.markdown")
    if path is None:
        return [Item(False, "Has a README.md", "no README.md at the top of the repo")]
    text = repo.text(path, 1_000_000) or ""
    doc = readme.Doc(text)
    items = readme.check(project, doc, repo.files, path, repo.owner, repo.name)
    shown = readme.images(doc, "", repo.owner, repo.name)
    for chart in project.charts:
        where = readme.chart_path(chart, shown, repo.files) or next(
            (f for f in sorted(repo.files) if posixpath.basename(f) == chart), None)
        if where is None:
            continue
        data = repo.read(where, filecheck.MAX_CHART_BYTES)
        problem = "bigger than 10 MB" if data is None else filecheck.chart_problem(data)
        items.append(Item(not problem, f"{chart} is a real chart", problem))
    out.readme_sha256 = readme.fingerprint(doc, project.ladder)
    return items


def pinned_item(repo) -> Item:
    text = repo.text("requirements.txt")
    what = "requirements.txt pins every package (name==version)"
    if text is None:
        return Item(False, what, "no requirements.txt at the top of the repo")
    reqs, problems = filecheck.parse_requirements(text)
    if problems:
        return Item(False, what, problems[0])
    loose = filecheck.unpinned(reqs)
    if not reqs or loose:
        return Item(False, what, f"not pinned: {', '.join(loose[:5])}" if loose else "it's empty")
    return Item(True, what)


def ladder_tests(ctx: Context, repo, project) -> list:
    sources = {p: repo.text(p) or "" for p in sorted(repo.files)
               if p.startswith("starter/") and p.endswith(".py")}
    modules = {p.split("/")[1].removesuffix(".py") for p in sources}
    check = scan_item(sources, True, modules,
                      ctx.sandbox.installed_modules(ctx.sandbox.club_python))
    what = f"of the club's tests pass ({project.tests})"
    if not check.ok:
        return [check, Item(False, f"0/{project.expected} {what}",
                            "not run until the safety read-through passes")]
    test = trusted.ladder_path(ctx.trusted, project)
    expected = trusted.test_names(test.read_text(encoding="utf-8"))
    repo.checkout()
    ctx.sandbox.adopt(repo.path)
    outcome = ctx.sandbox.harness(ctx.sandbox.club_python,
                                  ["file", str(test), str(repo.path / "tests" / project.tests),
                                   "starter"], cwd=repo.path, timeout=rules.TEST_MINUTES * 60)
    return [check, tests_item(outcome, expected, what)]
