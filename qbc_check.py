#!/usr/bin/env python3
"""Quantum Builders Club automatic checker: checks certificate and
milestone claims, reports on them, and issues the certificates that pass.
Python 3.13 on the club's runners; standard library only (plus the club's
stack inside the sandbox). skool/certificates/README.md explains the flow.

In the public claim forms repo (Ganamedee/qbc-certificates), its workflows run:
  python3 qbc_check.py setup                 make the sandbox (user, folders, club stack)
  python3 qbc_check.py selftest              prove the sandbox works before trusting it
  python3 qbc_check.py check --event FILE --out result.json
  python3 qbc_check.py report --event FILE --result result.json --check-job success --run-url URL
  python3 qbc_check.py sweep                 tell members their certificates are live

In this repo (verify-site/):
  python3 qbc_check.py issue-pending (--list | --write)   issue what passed (certificates.yml)
  python3 qbc_check.py publish --out DIR     write the claim forms repo's files
  python3 qbc_check.py check --claim club_foundations --login ada --name "Ada Lovelace" \\
      --repo https://github.com/ada/quantum-coin-flip [...] --unsafe-no-sandbox
                                             try a claim on your own machine (your own code only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from checker import PUBLIC_MODULES, claims, gh, rules, trusted  # noqa: E402
from qbc_validate import QbcError  # noqa: E402


def settings() -> dict:
    """base_url and claims_repo: from site.json in the claims repo, else the defaults."""
    found = {"base_url": rules.BASE_URL, "claims_repo": rules.CLAIMS_REPO}
    site = HERE / "site.json"
    if site.exists():
        found.update(json.loads(site.read_text()))
    found["claims_repo"] = os.environ.get("QBC_CLAIMS_REPO") or found["claims_repo"]
    return found


def trusted_dir() -> Path:
    """The club's tests: trusted/ in the claims repo, else built from this repo."""
    if (HERE / "trusted").is_dir():
        return HERE / "trusted"
    return trusted.build(Path(tempfile.mkdtemp(prefix="qbc-trusted-")) / "trusted")


def rules_version(folder: Path) -> str:
    """A short fingerprint of the checker's code and the club's tests, so a
    result says which rules it was checked against."""
    digest = hashlib.sha256()
    paths = [HERE / "checker" / f"{m}.py" for m in PUBLIC_MODULES] + \
        [HERE / "qbc_check.py", HERE / "qbc_validate.py"] + sorted(folder.rglob("*.py"))
    for path in paths:
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:12]


def make_sandbox(args):
    from checker.sandbox import Sandbox
    return Sandbox(root=args.sandbox_root, opt=args.sandbox_opt, unsafe=args.unsafe,
                   club_python=args.club_python)


def client() -> gh.GitHub:
    return gh.GitHub(token=os.environ.get("GITHUB_TOKEN"))


# ── Commands ────────────────────────────────────────────────────────────────
def cmd_setup(args) -> int:
    make_sandbox(args).setup(trusted_dir())
    print("Sandbox ready.")
    return 0


def cmd_selftest(args) -> int:
    problems = make_sandbox(args).selftest()
    for problem in problems:
        print(f"FAIL  {problem}")
    print("Sandbox selftest " + ("failed." if problems else "passed."))
    return 1 if problems else 0


def request_from_event(path: str):
    """The claim an issues or issue_comment event is about, or a reason to skip it."""
    event = json.loads(Path(path).read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    comment = event.get("comment")
    if comment is not None:
        if (comment.get("user") or {}).get("id") != (issue.get("user") or {}).get("id") \
                or not (comment.get("body") or "").strip().startswith("/recheck"):
            return None, "a comment that isn't the author's /recheck"
    if issue.get("state") != "open" or issue.get("pull_request"):
        return None, "not an open issue"
    if f"### {claims.FIELD_CLAIM}" not in (issue.get("body") or ""):
        return None, "not a claim form"
    try:
        return claims.read_issue(issue), ""
    except QbcError as error:
        return None, str(error)


def cmd_check(args) -> int:
    from checker.checks import Context, check_claim
    if args.event:
        request, skip = request_from_event(args.event)
        if request is None:
            print(f"Nothing to check: {skip}.")
            if args.out:
                Path(args.out).write_text(json.dumps({"skip": skip}))
            return 0
    else:
        if not (args.claim and args.login):
            raise QbcError("Give --event, or --claim, --login and --repo.")
        request = claims.from_args(args.claim, args.login, args.repo or [], name=args.name or "")
    folder = trusted_dir()
    sandbox = make_sandbox(args)
    if not (Path(sandbox.opt) / "trusted").is_dir():
        sandbox.setup(folder)
    ctx = Context(gh=client(), sandbox=sandbox, rules_version=rules_version(folder),
                  club_accounts=frozenset(a.lower() for a in rules.CLUB_ACCOUNTS))
    res = check_claim(request, ctx)
    print(summary(res))
    if args.out:
        Path(args.out).write_text(json.dumps(res.to_json(), indent=2, ensure_ascii=False))
    return 0


def summary(res) -> str:
    lines = [f"{'PASSED' if res.passed else 'NOT PASSED'}: {res.title} for {res.login}"]
    lines += [f"  problem: {p}" for p in res.problems]
    for repo in res.repos:
        lines.append(f"  {repo.label}: {repo.url or '(left empty)'}")
        lines += [f"    {'ok  ' if i.ok else 'FAIL'} {i.text}" + (f": {i.detail}" if i.detail else "")
                  for i in repo.items]
    return "\n".join(lines)


def cmd_report(args) -> int:
    from checker import report
    found = settings()
    repo = os.environ.get("GITHUB_REPOSITORY") or found["claims_repo"]
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    outcome = report.post(client(), repo, event, Path(args.result), args.check_job, args.run_url,
                          forms_url=f"https://github.com/{repo}/issues/new/choose",
                          guide_url=f"https://github.com/{repo}#what-the-checker-looks-at")
    print(f"Reported: {outcome}")
    return 0


def cmd_sweep(args) -> int:
    from checker.sweep import sweep
    found = settings()
    repo = os.environ.get("GITHUB_REPOSITORY") or found["claims_repo"]
    for line in sweep(client(), repo, found["base_url"].rstrip("/")) or ["Nothing to do."]:
        print(line)
    return 0


def cmd_issue_pending(args) -> int:
    import qbc_cert
    from checker import issuer
    site = qbc_cert.Site(args.root)
    repo = settings()["claims_repo"]
    decisions = issuer.pending(site, client(), repo)
    act = [d for d in decisions if d.action != "skip"]
    for d in decisions:
        print(f"#{d.issue} {d.login} {d.claim or '?'}: {d.action}" + (f" ({d.reason})" if d.reason else ""))
    if args.github_output:
        with open(args.github_output, "a") as out:
            out.write(f"pending={sum(1 for d in act if d.action == 'issue')}\n"
                      f"changes={len(act)}\n")
    if not args.write:
        print(f"{len(act)} to act on. Nothing written: add --write.")
        return 0
    for d in act:
        try:
            status = issuer.apply(site, repo, d, write=True)
        except QbcError as error:
            d.action, d.reason = "refused", str(error)
            status = issuer.apply(site, repo, d, write=True)
        print(f"#{d.issue}: {status['status']} {status.get('code', '')}".rstrip())
        if status.get("render_error"):
            warn = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "  warning: "
            print(f"{warn}#{d.issue}: no certificate image ({status['render_error']}). "
                  f"Draw it later with: python3 qbc_cert.py render {status.get('code', '')}")
    return 0


def cmd_publish(args) -> int:
    from checker.publish import publish
    found = settings()
    written = publish(Path(args.out), args.claims_repo or found["claims_repo"],
                      (args.base_url or found["base_url"]).rstrip("/"))
    print(f"Wrote {len(written)} files and folders to {args.out}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qbc_check.py", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def sandbox_options(cmd):
        cmd.add_argument("--unsafe-no-sandbox", dest="unsafe", action="store_true",
                         help="run tests as yourself, unisolated (your own code only)")
        cmd.add_argument("--club-python", help="a Python that already has the club's stack")
        cmd.add_argument("--sandbox-root", default="/srv/qbc")
        cmd.add_argument("--sandbox-opt", default="/opt/qbc")

    for name, func, text in (("setup", cmd_setup, "make the sandbox"),
                             ("selftest", cmd_selftest, "prove the sandbox works")):
        cmd = sub.add_parser(name, help=text)
        sandbox_options(cmd)
        cmd.set_defaults(func=func)
    cmd = sub.add_parser("check", help="check a claim")
    sandbox_options(cmd)
    cmd.add_argument("--event", help="a GitHub issues or issue_comment event payload")
    cmd.add_argument("--claim", choices=list(rules.CLAIMS_BY_ID))
    cmd.add_argument("--login", help="the GitHub username making the claim")
    cmd.add_argument("--name", help="the name to print")
    cmd.add_argument("--repo", action="append", help="a repo link, in the form's order")
    cmd.add_argument("--out", help="write the result here (JSON)")
    cmd.set_defaults(func=cmd_check)
    cmd = sub.add_parser("report", help="post a check's result on its claim")
    cmd.add_argument("--event", required=True)
    cmd.add_argument("--result", required=True)
    cmd.add_argument("--check-job", default="success")
    cmd.add_argument("--run-url", default="")
    cmd.set_defaults(func=cmd_report)
    cmd = sub.add_parser("sweep", help="tell members about issued certificates")
    cmd.set_defaults(func=cmd_sweep)
    cmd = sub.add_parser("issue-pending", help="issue the certificates whose claims passed")
    which = cmd.add_mutually_exclusive_group(required=True)
    which.add_argument("--list", action="store_true")
    which.add_argument("--write", action="store_true")
    cmd.add_argument("--root", default=str(HERE))
    cmd.add_argument("--github-output", help="append pending=N for a workflow step")
    cmd.set_defaults(func=cmd_issue_pending)
    cmd = sub.add_parser("publish", help="write the claim forms repo's files")
    cmd.add_argument("--out", required=True)
    cmd.add_argument("--claims-repo")
    cmd.add_argument("--base-url")
    cmd.set_defaults(func=cmd_publish)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except QbcError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except gh.GitHubError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
