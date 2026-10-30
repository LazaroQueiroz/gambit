# gambit

Learn chess openings in the terminal: 3,865 named lines (Lichess's CC0 dataset) plus 83 hand-written lessons.

    gambit                         open the menu
    gambit --learn "italian"       start a guided lesson
    gambit --test "najdorf"        start a test
    gambit --list "french"         print matching lines (* = has a lesson)

## Install

Requires Python 3.11 or newer.

    git clone <repo-url> gambit
    cd gambit
    python -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    ./setup-engine.sh              # optional: downloads Stockfish 19 (about 80 MB)
    .venv/bin/gambit

Install with `-e` so gambit can find the Stockfish that the script puts in `engine/`.

## Stockfish

Without Stockfish, a basic built-in search is used, which only sees short tactics. `setup-engine.sh`
downloads the official Linux x86-64 release and verifies its checksum. On other platforms, install
Stockfish yourself. gambit looks for an engine in this order:

1. `--engine PATH`
2. the `GAMBIT_ENGINE` environment variable
3. `engine/stockfish/stockfish-*`
4. `stockfish` on your `PATH`

Any UCI engine works.

## What you can do

The menu offers:

- **Browse openings:** search by name, ECO code or moves, then learn or test any line
- **Random test:** from the openings with a lesson, or from the full book
- **Review due lines:** spaced repetition of what you have already drilled
- **Free board:** play any moves and get them explained
- **Name that opening:** a quiz
- **Your progress:** your saved results and streak

## Keys on the board

Move with the mouse (click or drag), by typing (`Nf3`, `exd5`, `O-O`, `e2e4`, then Enter), or with
the arrow keys and Enter or Space.

| Key | Action |
| --- | --- |
| `i` | hint: first the piece, then the move |
| `s` | show the answer and why it works |
| `u` | take back your move and try again |
| `p` | play on against the engine after a wrong move |
| `v` | flip the board |
| `r` | restart this drill |
| `n` | next drill, when one is finished |
| `q` | back to the previous screen |
| `?` | help |

Verdicts compare winning chances before and after your move. Book means a named line contains it.
Inaccuracy, Mistake and Blunder mean a loss of 5, 10 and 20 percent of winning chances.

## Command line options

| Option | Effect |
| --- | --- |
| `--list [QUERY]` | print matching openings and exit |
| `--learn QUERY` | start in a guided lesson of the first match |
| `--test QUERY` | start in a test of the first match |
| `--side {white,black}` | side to play with `--learn` or `--test` |
| `--engine PATH` | path to a UCI engine |
| `--time SECONDS` | seconds per position for Stockfish (default 0.5) |
| `--no-stockfish` | use only the built-in evaluator |

## Where data is stored

| Path | Contents |
| --- | --- |
| `~/.local/share/gambit/progress.json` | your progress |
| `~/.local/share/gambit/analysis.sqlite` | cached engine analysis |
| `~/.cache/gambit/` | the compiled opening index |

`XDG_DATA_HOME` and `XDG_CACHE_HOME` are respected.

## Development

Run the tests with:

    GAMBIT_ENGINE= .venv/bin/python -m pytest

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the code fits together.

## License

gambit is free software under the [GNU General Public License v3.0 or later](LICENSE).

## Credits

- Opening names and moves come from the [Lichess chess-openings](https://github.com/lichess-org/chess-openings) dataset (CC0).
- [Stockfish](https://stockfishchess.org) is GPLv3. It is downloaded separately and is not part of this repository.
