# Architecture

gambit is a [Textual](https://textual.textualize.io) app built on [python-chess](https://python-chess.readthedocs.io).
The training rules know nothing about the interface, so they can be tested without a terminal.

    __main__.py   command line
        |
      app.py      screens, keys, background work
        |    \
        |   board.py    board and evaluation bar widgets
        |
     drill.py     rules of a training session
      /     \
    book.py  explain.py   what the moves are / what a move did
      |          |
    curated.py  engine.py  lessons / position evaluation

    progress.py   saved results and spaced repetition

## Modules

| Module | Responsibility |
| --- | --- |
| `gambit/book.py` | Loads `data/*.tsv`, merges the lessons in, and answers searches and position lookups. The compiled index is pickled to the cache directory. |
| `gambit/curated.py` | The hand-written lessons: summaries, plans, annotated main lines, tempting mistakes and sidelines. |
| `gambit/engine.py` | `Evaluator` is a thread-safe front end with a SQLite cache, over either `UciEngine` (Stockfish) or `BuiltinEngine` (a small fallback search). |
| `gambit/explain.py` | Turns a move plus engine numbers into a plain-language `Feedback`. |
| `gambit/drill.py` | `Drill` tracks one session in `test`, `learn` or `free` mode and scores each move. |
| `gambit/progress.py` | Leitner-box spaced repetition, streaks and quiz totals, saved as JSON. |
| `gambit/board.py` | The `ChessBoard` and `EvalBar` widgets. |
| `gambit/app.py` | All screens: menu, browse, drill, quiz, stats and help. |

## Key ideas

**Notes attach to positions, not lines.** A lesson's note is stored against (position, move), so it
also appears in any other line that reaches the same position, including by transposition.

**Explanations are computed.** `explain.py` derives what it says from the actual position (attacks,
exchanges, pawn structure, the engine's line), so it stays accurate on lines nobody annotated.

**Verdicts use winning chances, not centipawns.** `win_percent` converts a score into winning
chances, and a verdict depends on how much the move lost. A gambit the engine agrees with is not
called a blunder just because it gives up material.

**Analysis never blocks the interface.** The app evaluates on background workers. `DrillScreen`
bumps a token whenever the position changes and discards results that carry an old one.

**Spaced repetition.** Each line sits in a Leitner box with a review interval of 0, 1, 3, 7, 14, 30
or 60 days. A line counts as mastered from box 4.

## Data

`gambit/data/{a,b,c,d,e}.tsv` hold one opening per row, split by ECO letter:

    eco	name	pgn
    A00	Amar Opening	1. Nh3

## Adding a lesson

Add an `O(...)` entry to `OPENINGS` in `gambit/curated.py`. In the main line, `{braces}` annotate the
move before them. The name must match a name in the dataset, and the tests check this along with the
legality of every line and mistake.

## Tests

`tests/test_gambit.py` covers the book, explanations, engines, drills, progress and the interface
(through Textual's `run_test` pilot). `tests/fake_uci.py` is a stand-in UCI engine, so the integration
tests need no Stockfish. Setting `GAMBIT_ENGINE=` to empty makes the suite use the built-in search.
