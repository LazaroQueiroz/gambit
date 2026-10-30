"""Command line entry point."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="gambit", description="Learn chess openings in the terminal.")
    parser.add_argument("--engine", help="path to a UCI engine (default: find Stockfish)")
    parser.add_argument("--time", type=float, default=0.5, help="seconds per position for Stockfish (default 0.5)")
    parser.add_argument("--no-stockfish", action="store_true", help="use only the built-in evaluator")
    parser.add_argument("--list", metavar="QUERY", nargs="?", const="", help="print matching openings and exit")
    parser.add_argument("--test", metavar="QUERY", help="start straight in a test of the first match")
    parser.add_argument("--learn", metavar="QUERY", help="start straight in a guided lesson of the first match")
    parser.add_argument("--side", choices=("white", "black"), help="side to play with --test or --learn")
    args = parser.parse_args()

    from .book import Book

    book = Book()
    if args.list is not None:
        for line in book.search(args.list):
            print(f"{'*' if line.curated else ' '} {line.eco}  {line.name}  |  {line.pretty}")
        return 0

    start = None
    query = args.test or args.learn
    if query:
        lowered = query.lower()
        matches = sorted(book.search(query), key=lambda l: not l.name.lower().startswith(lowered))
        if not matches:
            print(f"No opening matches {query!r}. Try: gambit --list {query.split()[0]}", file=sys.stderr)
            return 1
        import chess

        from .app import DrillSpec
        from .drill import LEARN, TEST

        line = matches[0]
        side = line.default_side if not args.side else args.side == "white"
        start = DrillSpec([line], side, TEST if args.test else LEARN, line.name)

    from .app import GambitApp
    from .engine import Evaluator
    from .progress import Progress

    evaluator = Evaluator(args.engine, seconds=args.time, use_stockfish=not args.no_stockfish)
    GambitApp(book, evaluator, Progress(), start).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
