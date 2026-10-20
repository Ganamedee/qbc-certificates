"""The flagship seasons the engine scores, and where it keeps their files in the claims repo."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

KEY_FILE = "seasons/sealing-key.txt"        # the public half of the season key, written by the post job
COMMENT_TAG = "<!-- qbc-seasons"            # starts the hidden line that marks the workflow's own comments
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


SEASONS = {
    1: Season(1, "Quantum Circuit Golf", "⛳", "season-01-circuit-golf.yml", "Your Season 1 card", "seasons/s1",
              sealed=True, colour="2e8b57", command="python grade.py --card {login}", kind="golf",
              pack="projects/flagships/season-01-circuit-golf", files=("grade.py", "holes/hole*.json")),
    2: Season(2, "Beat Einstein", "🔔", "season-02-chsh.yml", "Your Season 2 card", "seasons/s2",
              sealed=True, colour="b8860b", command="python referee.py --card {login}", kind="chsh",
              pack="projects/flagships/season-02-chsh", files=("referee.py",)),
}


def season_of(body: str | None) -> Season | None:
    """The season an issue body is an entry for: the first season whose form field heading it has."""
    lines = {line.strip() for line in (body or "").replace("\r\n", "\n").split("\n")}
    return next((s for s in SEASONS.values() if s.marker in lines), None)
