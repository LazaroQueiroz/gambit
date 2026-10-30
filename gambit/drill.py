"""The rules of a training session, independent of the interface."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import chess

from .book import Book, Line
from .engine import Analysis
from .explain import Feedback, assess

TEST, LEARN, FREE = "test", "learn", "free"


@dataclass
class Outcome:
    feedback: Feedback
    kind: str  # "correct", "alternative" (other book line), "wrong" or "free"
    line_over: bool = False


@dataclass
class Result:
    right: int
    total: int
    hints: int
    cp_loss: int
    errors: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        return round(100 * self.right / self.total) if self.total else 0


class Drill:
    def __init__(self, book: Book, lines: list[Line], side: chess.Color, mode: str = TEST,
                 rng: random.Random | None = None) -> None:
        self.book = book
        self.lines = lines
        self.side = side
        self.mode = mode
        self.rng = rng or random.Random()
        self.board = chess.Board()
        self.sans: list[str] = []
        self.off_book = mode == FREE
        self.right = self.total = self.hints = self.cp_loss = 0
        self.errors: list[str] = []
        self._first_try = True
        self._hinted = False
        self.finished = False
        self.reached: Line | None = lines[0] if len(lines) == 1 else None

    # ------------------------------------------------------------ book logic
    def expected(self) -> list[str]:
        """Book moves from this drill's lines at the current position."""
        if self.off_book:
            return []
        depth = len(self.sans)
        prefix = tuple(self.sans)
        found = []
        for line in self.lines:
            if len(line.moves) > depth and line.moves[:depth] == prefix and line.moves[depth] not in found:
                found.append(line.moves[depth])
        return found

    def is_user_turn(self) -> bool:
        return self.mode == FREE or self.off_book or self.board.turn == self.side

    def line_complete(self) -> bool:
        return not self.off_book and not self.expected()

    def _note_reached(self) -> None:
        prefix = tuple(self.sans)
        for line in self.lines:
            if line.moves == prefix:
                self.reached = line
                return
        if self.reached is None:
            self.reached = next((l for l in self.lines if l.moves[:len(prefix)] == prefix), None)

    # ---------------------------------------------------------------- moves
    def push(self, move: chess.Move) -> None:
        self.sans.append(self.board.san(move))
        self.board.push(move)
        self._note_reached()

    def undo(self) -> None:
        if self.board.move_stack:
            self.board.pop()
            self.sans.pop()

    def judge(self, before_board: chess.Board, move: chess.Move, before: Analysis, after: Analysis,
              reliable: bool = True) -> Outcome:
        """Classify a move the user just played; the move is already on self.board."""
        feedback = assess(self.book, before_board, move, before, after, reliable)
        if self.mode == FREE or self.off_book:
            return Outcome(feedback, "free")
        wanted = self._expected_at(before_board)
        if feedback.san in wanted:
            self._count_move(feedback)
            self._first_try = True
            self._hinted = False
            return Outcome(feedback, "correct", self.line_complete())
        if feedback.in_book and feedback.verdict in ("book", "good", "best"):
            return Outcome(feedback, "alternative")
        if self._first_try:
            self.errors.append(f"{feedback.san} ({feedback.verdict})")
        self._first_try = False
        return Outcome(feedback, "wrong")

    def _expected_at(self, before_board: chess.Board) -> list[str]:
        depth = len(self.sans) - 1
        prefix = tuple(self.sans[:depth])
        return list({line.moves[depth] for line in self.lines
                     if len(line.moves) > depth and line.moves[:depth] == prefix})

    def _count_move(self, feedback: Feedback) -> None:
        self.total += 1
        if self._first_try and not self._hinted:
            self.right += 1
        self.cp_loss += max(0, feedback.cp_before - feedback.cp_after)

    def choose_reply(self) -> chess.Move | None:
        options = self.expected()
        if not options:
            return None
        return self.board.parse_san(self.rng.choice(options))

    def use_hint(self) -> None:
        if not self._hinted:
            self.hints += 1
        self._hinted = True

    def leave_book(self) -> None:
        """The student chose to play on against the engine after a deviation."""
        self.off_book = True

    def result(self) -> Result:
        return Result(self.right, self.total, self.hints, self.cp_loss, self.errors)
