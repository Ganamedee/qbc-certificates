"""Build Sprint Graduate, checked from the sprint repo itself, so nobody keeps
a list of graduates. GitHub logs every push to a repo with the time it
arrived (the repo's Activity tab), and unlike a commit's date, nobody can set
that time. From the member's own pushes, and the README each one left:

- the goal: their first push with a Goal section in the README starts the
  sprint, as week 0;
- the weeks: pushes of theirs in at least 4 of the 6 weeks after week 0;
- the ship: a What I shipped section, pushed by the end of week 6.

course/certificates/README.md section 1 is the source; the numbers are in
rules.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import readme, rules
from .result import Item

README_NAMES = ("README.md", "README.markdown", "README.rst", "README.txt", "README")
GOAL, SHIPPED = r"\bgoals?\b", r"\bshipped\b"
WEEK = timedelta(days=7)
NEW_SPRINT = "A new sprint needs a new repo, with a new goal"


def check(ctx, repo, login: str, user_id: int) -> list:
    if repo.find(*README_NAMES) is None:
        return [Item(False, "Has a README", "add one: your goal and what you shipped go in it")]
    items = [Item(True, "Has a README")]
    pushes = sorted((p for p in ctx.gh.activity(repo.owner, repo.name)
                     if _yours(p, login, user_id) and p.get("activity_type") != "branch_deletion"
                     and _time(p.get("timestamp"))), key=lambda p: _time(p["timestamp"]))
    docs = {}

    def words(push, pattern):
        """Words in that README section as this push left it, code blocks
        included (None: no such section)."""
        sha = str(push.get("after") or "")
        if sha not in docs:
            text = repo.text_at(sha, README_NAMES)
            docs[sha] = None if text is None else readme.Doc(text)
        body = docs[sha].section(pattern, code=True) if docs[sha] else None
        return None if body is None else len(readme.words(body))

    what = "Your goal, in a Goal section of the README"
    goal = next((p for p in pushes if (words(p, GOAL) or 0) >= rules.SPRINT_GOAL_WORDS), None)
    if goal is None:
        found = _most(words(p, GOAL) for p in pushes)
        if not pushes:
            detail = (f"GitHub has nothing pushed to this repo by {login}: push from the account "
                      "that opened this claim")
        elif found is None:
            detail = ("no README you pushed has a Goal section. Write what you'll ship under a Goal "
                      "heading and push it: your sprint starts that day")
        else:
            detail = (f"your Goal section has {found} words; write at least "
                      f"{rules.SPRINT_GOAL_WORDS} and push it: your sprint starts that day")
        return items + [Item(False, what, detail)]
    start = _time(goal["timestamp"])
    end = start + WEEK * (rules.SPRINT_WEEKS + 1)
    now = _time(ctx.now()) or end
    items.append(Item(True, what, f"first pushed {_when(start)}, which starts your sprint; week 6 "
                                  f"{'ends' if now < end else 'ended'} {_when(end)}"))

    what = f"Work pushed in at least {rules.SPRINT_MIN_WEEKS} of the {rules.SPRINT_WEEKS} weeks after it"
    weeks = sorted({(_time(p["timestamp"]) - start) // WEEK for p in pushes}
                   & set(range(1, rules.SPRINT_WEEKS + 1)))
    left = [w for w in range(max((now - start) // WEEK, 1), rules.SPRINT_WEEKS + 1) if w not in weeks]
    so_far = _weeks(weeks) if weeks else "none yet"
    need = rules.SPRINT_MIN_WEEKS - len(weeks)
    if need <= 0:
        items.append(Item(True, what, _weeks(weeks)))
    elif now >= end:
        items.append(Item(False, what, f"{so_far}, and week 6 ended {_when(end)}. {NEW_SPRINT}"))
    elif len(left) < need:
        items.append(Item(False, what, f"{so_far}, and with {_weeks(left) or 'no weeks'} left this "
                                       f"sprint can't reach {rules.SPRINT_MIN_WEEKS}. {NEW_SPRINT}"))
    else:
        items.append(Item(False, what, f"{so_far}. Push work in {need}{' more' if weeks else ''} of "
                                       f"{_weeks(left)}, then comment /recheck"))

    what = "What I shipped, in the README by the end of week 6"
    window = [p for p in pushes if start <= _time(p["timestamp"]) < end]
    done = next((p for p in window if (words(p, SHIPPED) or 0) >= rules.SPRINT_SHIPPED_WORDS), None)
    found = _most(words(p, SHIPPED) for p in window)
    least = rules.SPRINT_SHIPPED_WORDS
    if done:
        detail = f"pushed {_when(_time(done['timestamp']))}"
    elif now >= end:
        detail = (f"no README you pushed by {_when(end)}, when week 6 ended, has one" if found is None
                  else f"it had {found} words when week 6 ended ({_when(end)}); it needed {least}")
        detail += f". {NEW_SPRINT}"
    elif found is None:
        detail = (f"add a What I shipped section (what it does, how to run it and what you learned, "
                  f"at least {least} words) and push it before {_when(end)}")
    else:
        detail = f"it has {found} words; write at least {least} and push it before {_when(end)}"
    items.append(Item(bool(done), what, detail))
    return items


def _most(counts):
    return max((n for n in counts if n is not None), default=None)


def _yours(push: dict, login: str, user_id: int) -> bool:
    actor = push.get("actor") or {}
    if user_id:
        return actor.get("id") == user_id
    return str(actor.get("login", "")).lower() == login.lower()


def _time(stamp):
    """An aware datetime from GitHub's ISO time, or None."""
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _when(moment) -> str:
    return moment.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC").lstrip("0")


def _weeks(weeks) -> str:
    numbers = [str(n) for n in weeks]
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"week {numbers[0]}"
    return f"weeks {', '.join(numbers[:-1])} and {numbers[-1]}"
