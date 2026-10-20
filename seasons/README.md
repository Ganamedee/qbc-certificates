# The club's flagship seasons

Season entries for the Quantum Builders Club, scored by the `seasons` workflow and shown on live boards. Every season is always open: enter whenever you like, as often as you like.

- ⛳ Season 1: Quantum Circuit Golf: [live board](https://github.com/Ganamedee/qbc-certificates/blob/HEAD/seasons/boards/season-01.md) · [entry form](https://github.com/Ganamedee/qbc-certificates/issues/new?template=season-01-circuit-golf.yml)
- 🔔 Season 2: Beat Einstein: [live board](https://github.com/Ganamedee/qbc-certificates/blob/HEAD/seasons/boards/season-02.md) · [entry form](https://github.com/Ganamedee/qbc-certificates/issues/new?template=season-02-chsh.yml)

## How an entry is scored

1. In the season's pack you run the command its README gives (Season 1: `python grade.py --card YOUR_GITHUB_USERNAME`; Season 2: `python referee.py --card YOUR_GITHUB_USERNAME`). It scores your work on your machine, then seals it to the club's season key, published in [`seasons/sealing-key.txt`](seasons/sealing-key.txt). Only the scoring workflow can open a sealed card, and a card only counts for the GitHub account it names.
2. You paste the card into the season's entry form. The workflow opens it, scores it again with the same grader as the pack, and comments with your score and where it puts you. Scored entries are closed; one that can't be scored stays open with a comment saying how to fix it.
3. Each season's board in `seasons/boards` is rewritten whenever a score changes: a ranked season shows its top 10 and the bars anyone can clear, a badge season (Season 2) everyone past each bar. Only scores are public, never anyone's circuits.

To send a better card, edit your entry and paste it in, or enter again. An entry counts from when it was opened, or from its last edit: on a ranked board a tie goes to whoever made that score first, and a badge board lists builders in the order they reached each bar.

## Running it

The workflow needs one repository secret, set once: `SEASONS_SEED`, any random text of 32 characters or more (a password manager's generator is ideal). It never appears in a log or a file: the workflow derives the season key from it and publishes only the public half, in `seasons/sealing-key.txt`, on its first run. Run the workflow once from the Actions tab (Run workflow) after setting the secret, so the key is there before anyone needs it. Changing the secret changes the key, and cards sealed with the old key won't open.

Everything else runs itself: each entry is scored when it's opened or edited, and every six hours the workflow looks again at everything changed in the last 14 days. Close an entry as not planned to take it off the board (its author can do the same to withdraw it); reopen it to put it back. If a season's grader changes, every entry in it is scored again on the next run.

The workflow commits the boards and its state to the default branch as github-actions[bot], so that branch must let GitHub Actions push.

## Files

- `.github/ISSUE_TEMPLATE/season-*.yml`, `.github/workflows/seasons.yml`, `seasons/engine`, each season's grader folder (`seasons/s1`, `seasons/s2`, ...) and this README are published from the club's repo by `seasons/publish.py`: edit them there, not here.
- `seasons/boards`, `seasons/state` and `seasons/sealing-key.txt` belong to the workflow. Don't edit them by hand.
