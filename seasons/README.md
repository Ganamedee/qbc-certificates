# The club's flagship seasons

Season entries for the Quantum Builders Club, scored by the `seasons` workflow and shown on live boards. Every season is always open: enter whenever you like. Season 3 gives each GitHub account one case and one set of calls (below); every other season takes as many entries as you like.

- ⛳ Season 1: Quantum Circuit Golf: [live board](https://github.com/Ganamedee/qbc-certificates/blob/HEAD/seasons/boards/season-01.md) · [send a card on the club site](https://quantumbuilders.club/season) · [entry form](https://github.com/Ganamedee/qbc-certificates/issues/new?template=season-01-circuit-golf.yml)
- 🔔 Season 2: Beat Einstein: [live board](https://github.com/Ganamedee/qbc-certificates/blob/HEAD/seasons/boards/season-02.md) · [send a card on the club site](https://quantumbuilders.club/season) · [entry form](https://github.com/Ganamedee/qbc-certificates/issues/new?template=season-02-chsh.yml)
- 🕵️ Season 3: Rigged RNG Hunt: [live board](https://github.com/Ganamedee/qbc-certificates/blob/HEAD/seasons/boards/season-03.md) · [open your case on the club site](https://quantumbuilders.club/season#case-3) · [entry form](https://github.com/Ganamedee/qbc-certificates/issues/new?template=season-03-rng-hunt.yml)

## How an entry is scored

1. In the season's pack you run the command its README gives (Season 1: `python grade.py --card YOUR_GITHUB_USERNAME`; Season 2: `python referee.py --card YOUR_GITHUB_USERNAME`; Season 3: `python detective.py --card YOUR_GITHUB_USERNAME`). It seals your work to the club's season key, published in [`seasons/sealing-key.txt`](seasons/sealing-key.txt), so only the scoring workflow can open it, and a card only counts for the GitHub account it names. Seasons 1 and 2's packs score your work first, on your machine; Season 3's answers stay with the workflow.
2. You send the card on [the club's season page](https://quantumbuilders.club/season), which files it here as an entry opened by your own GitHub account (Season 3's goes into the entry that opened your case), or you paste it into the season's entry form. The workflow opens it, scores it with the same grader as the pack, and comments with your score and where it puts you. Scored entries are closed; one that can't be scored stays open with a comment saying how to fix it.
3. Each season's board in `seasons/boards` is rewritten whenever a score changes: a ranked season shows its top 10 and the bars anyone can clear, a badge season (Seasons 2 and 3) everyone past each bar. Only scores are public, never anyone's circuits or calls.

Outside Season 3, send a better card whenever you like: edit your entry and paste it in, or enter again. An entry counts from when it was opened, or from its last edit: on a ranked board a tie goes to whoever made that score first, and a badge board lists builders in the order they reached each bar.

## Season 3: one case and one set of calls

Season 3 gives every GitHub account a case of its own: twelve files of bits, three to five of them rigged, made from the `SEASONS_SEED` secret and the account's id, so it can never be re-rolled and nobody else's case says anything about yours. Open it on [the club's season page](https://quantumbuilders.club/season#case-3) with Open my case, or with the Season 3 entry form and the calls box empty: the workflow writes it to `seasons/cases/season-03/` (your username, lowercased, `.txt`) and comments with the command that downloads it. When your calls are ready, seal them with `python detective.py --card YOUR_GITHUB_USERNAME` and send the card into the same entry: Send your calls on its page on the club site, or edit the issue and paste it in.

Each account's first scored set of calls is its only one: the workflow keeps that score for good, and any later entry from the account isn't scored. Withdrawing the scored entry takes it off the board, but never gives the account another set of calls. The answers stay with the workflow: nothing here says which of anyone's files are rigged.

## Running it

The workflow needs one repository secret, set once: `SEASONS_SEED`, any random text of 32 characters or more (a password manager's generator is ideal). It never appears in a log or a file: the workflow derives the season key from it and publishes only the public half, in `seasons/sealing-key.txt`, on its first run. Run the workflow once from the Actions tab (Run workflow) after setting the secret, so the key is there before anyone needs it. Changing the secret changes the key, and cards sealed with the old key won't open. It also makes every Season 3 case, so once Season 3 has started, never change it: every case would change with it.

Everything else runs itself: each entry is scored when it's opened or edited, and every six hours the workflow looks again at everything changed in the last 14 days. Close an entry as not planned to take it off the board (its author can do the same to withdraw it); reopen it to put it back. If a season's grader changes, every entry in it is scored again on the next run.

The workflow commits the boards and its state to the default branch as github-actions[bot], so that branch must let GitHub Actions push.

## Files

- `.github/ISSUE_TEMPLATE/season-*.yml`, `.github/workflows/seasons.yml`, `seasons/engine`, each season's grader folder (`seasons/s1`, `seasons/s2`, ...) and this README are published from the club's repo by `seasons/publish.py`: edit them there, not here.
- `seasons/boards`, `seasons/state`, `seasons/cases` and `seasons/sealing-key.txt` belong to the workflow. Don't edit them by hand.
