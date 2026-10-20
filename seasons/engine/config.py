"""The flagship seasons the engine scores, and where it keeps their files in the claims repo."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .cases import CASE_DIR

KEY_FILE = "seasons/sealing-key.txt"        # the public half of the season key, written by the post job
COMMENT_TAG = "<!-- qbc-seasons"            # starts the hidden line that marks the workflow's own comments
# A line of its own in an entry: its author has taken it off the board until the line goes. The club site adds it
# (Take this entry off the board) and deletes it again (Put this entry back on the board), and the entry is the same
# entry either way: score.body_sha leaves the line out.
OFF_BOARD = "<!-- qbc-seasons: off the board -->"
# The label the workflow puts on an entry it takes off for that line, before it closes it as not planned, and takes
# off again once the entry is back: an entry closed as not planned without it was closed by a person.
OFF_LABEL = "off-the-board"
SITE = "https://quantumbuilders.club"       # the club website: its season page files cards here as the member
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")


@dataclass(frozen=True)
class Season:
    number: int
    name: str
    emoji: str
    form: str             # the issue form's file name in .github/ISSUE_TEMPLATE
    field: str            # the form field that holds the entry
    grader: str           # the folder, from the repo root, that holds the season's grader
    sealed: bool          # entries are sealed cards, opened with SEASONS_SEED
    colour: str           # the label's colour
    command: str          # what a member runs in the pack to make a card, with {login} for their username
    kind: str             # how its entries are scored and ranked: a module in kinds.KINDS
    pack: str             # the member pack in the club's repo, which seasons/publish.py copies the grader from
    files: tuple          # the grader's files in the pack (globs), copied into the grader folder
    on_site: bool = False  # members send its cards on the club site's season page, not with the issue form

    @property
    def tool(self) -> str:
        """The pack's script a member runs to check their work, such as grade.py."""
        return self.command.split()[1]

    @property
    def label(self) -> str:
        return f"season-{self.number:02d}"

    @property
    def marker(self) -> str:
        return f"### {self.field}"

    @property
    def title(self) -> str:
        return f"{self.emoji} Season {self.number}: {self.name}"

    def board(self, root: Path, suffix: str) -> Path:
        return Path(root) / "seasons" / "boards" / f"{self.label}.{suffix}"

    def state_file(self, root: Path) -> Path:
        return Path(root) / "seasons" / "state" / f"{self.label}.json"

    def form_url(self, repo: str) -> str:
        return f"https://github.com/{repo}/issues/new?template={self.form}"

    def board_url(self, repo: str) -> str:
        return f"https://github.com/{repo}/blob/HEAD/seasons/boards/{self.label}.md"

    def board_link(self, repo: str) -> str:
        """The live board the workflow's comments link: on the club site's season page, or on GitHub."""
        return f"{SITE}/season#season-{self.number}" if self.on_site else self.board_url(repo)

    def send(self, form_url: str) -> str:
        """How a member sends a card, as the workflow's words say it: on the club site, or with the issue form."""
        if self.on_site:
            return f"on the [club's season page]({SITE}/season)"
        return f"with the [Season {self.number} entry form]({form_url})"

    def entry_page(self, number: int) -> str:
        """An entry's own page on the club site, where its author sends or replaces the card in it."""
        return f"{SITE}/season/entry/{number}"

    def case_path(self, login: str) -> str:
        """A builder's case in the claims repo, from its root: seasons/cases/season-03/<login, lowercased>.txt."""
        if not isinstance(login, str) or not LOGIN.fullmatch(login):
            raise ValueError("a case file needs a GitHub username")
        return f"{CASE_DIR}/{self.label}/{login.lower()}.txt"

    def case_file(self, root: Path, login: str) -> Path:
        return Path(root) / self.case_path(login)

    def case_raw_url(self, repo: str, login: str) -> str:
        """Where the pack downloads a builder's case from."""
        return f"https://raw.githubusercontent.com/{repo}/main/{self.case_path(login)}"

    def case_page_url(self, repo: str, login: str) -> str:
        """The case's page on GitHub, to save it from the browser."""
        return f"https://github.com/{repo}/blob/main/{self.case_path(login)}"


SEASONS = {
    1: Season(1, "Quantum Circuit Golf", "⛳", "season-01-circuit-golf.yml", "Your Season 1 card", "seasons/s1",
              sealed=True, colour="2e8b57", command="python grade.py --card {login}", kind="golf",
              pack="projects/flagships/season-01-circuit-golf", files=("grade.py", "holes/hole*.json"),
              on_site=True),
    2: Season(2, "Beat Einstein", "🔔", "season-02-chsh.yml", "Your Season 2 card", "seasons/s2",
              sealed=True, colour="b8860b", command="python referee.py --card {login}", kind="chsh",
              pack="projects/flagships/season-02-chsh", files=("referee.py",), on_site=True),
    3: Season(3, "Rigged RNG Hunt", "🕵️", "season-03-rng-hunt.yml", "Your Season 3 calls", "seasons/s3",
              sealed=True, colour="6a5acd", command="python detective.py --card {login}", kind="detective",
              pack="projects/flagships/season-03-rng-hunt", files=("riggers.py",), on_site=True),
    4: Season(4, "Catch Eve", "🥷", "season-04-catch-eve.yml", "Your Season 4 card", "seasons/s4",
              sealed=True, colour="2f4f4f", command="python referee.py --card {login}", kind="eve",
              pack="projects/flagships/season-04-catch-eve", files=("referee.py",), on_site=True),
}


def season_of(body: str | None) -> Season | None:
    """The season an issue body is an entry for: the first season whose form field heading it has."""
    lines = {line.strip() for line in (body or "").replace("\r\n", "\n").split("\n")}
    return next((s for s in SEASONS.values() if s.marker in lines), None)
