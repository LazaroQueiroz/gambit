"""The opening book: named lines from the Lichess dataset plus curated notes."""

from __future__ import annotations

import hashlib
import os
import pickle
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import chess

from .curated import COMMON, OPENINGS, Curated

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "gambit"
INDEX_VERSION = 1
_TOKEN = re.compile(r"\{([^}]*)\}|(\S+)")
_MOVE_NO = re.compile(r"^\d+\.+")


def parse_annotated(text: str) -> list[tuple[str, str | None]]:
    """Parse "1.e4 {note} e5 2.Nf3" into [(san, note), ...]."""
    out: list[tuple[str, str | None]] = []
    for note, token in _TOKEN.findall(text):
        if note:
            out[-1] = (out[-1][0], note.strip())
            continue
        san = _MOVE_NO.sub("", token)
        if san:
            out.append((san, None))
    return out


@dataclass(frozen=True)
class Line:
    eco: str
    name: str
    moves: tuple[str, ...]
    curated: Curated | None = None

    @property
    def key(self) -> str:
        return " ".join(self.moves)

    @property
    def family(self) -> str:
        return self.name.split(":")[0].strip()

    @property
    def variation(self) -> str:
        return self.name.split(",")[0].strip()

    @property
    def default_side(self) -> chess.Color:
        if self.curated:
            return self.curated.side == "white"
        lowered = self.name.lower()
        black_words = ("defense", "defence", "countergambit", "counter-gambit")
        return not any(word in lowered for word in black_words)

    @cached_property
    def pretty(self) -> str:
        return pretty_moves(self.moves)


def pretty_moves(moves, start_ply: int = 0) -> str:
    parts = []
    for i, san in enumerate(moves, start_ply):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}.{san}")
        elif i == start_ply:
            parts.append(f"{i // 2 + 1}...{san}")
        else:
            parts.append(san)
    return " ".join(parts)


def _epd(board: chess.Board) -> str:
    return board.epd()


class Book:
    def __init__(self) -> None:
        self.lines: list[Line] = []
        self.curated_lines: list[Line] = []
        self.notes: dict[tuple[str, str], str] = {}
        self.mistakes: dict[tuple[str, str], str] = {}
        self.names_by_epd: dict[str, Line] = {}
        self.book_moves: dict[str, set[str]] = {}
        self._san_cache: dict[tuple[str, ...], chess.Board] = {}
        self._load_lichess()
        self._load_curated()
        cached = self._load_index_cache()
        if cached:
            self.names_by_epd, self.book_moves = cached
        else:
            self._index()
            self._save_index_cache()
        self._san_cache.clear()

    # ------------------------------------------------------------------ load
    def _load_lichess(self) -> None:
        for path in sorted(DATA_DIR.glob("*.tsv")):
            for row in path.read_text(encoding="utf-8").splitlines()[1:]:
                eco, name, pgn = row.split("\t")
                moves = tuple(san for san, _ in parse_annotated(pgn))
                self.lines.append(Line(eco, name, moves))

    def _load_curated(self) -> None:
        for opening in OPENINGS:
            parsed = parse_annotated(opening.line)
            moves = tuple(san for san, _ in parsed)
            self.curated_lines.append(Line(opening.eco, opening.name, moves, opening))
            self._absorb_notes(parsed)
            for extra in opening.sidelines:
                self._absorb_notes(parse_annotated(extra))
            for before, san, why in opening.mistakes:
                board = self._board_after(tuple(before.split()))
                self.mistakes[(_epd(board), board.parse_san(san).uci())] = why
        for text in COMMON:
            self._absorb_notes(parse_annotated(text))

    def _absorb_notes(self, parsed: list[tuple[str, str | None]]) -> None:
        board = chess.Board()
        for san, note in parsed:
            move = board.parse_san(san)
            if note:
                self.notes.setdefault((_epd(board), move.uci()), note)
            board.push(move)

    def _board_after(self, moves: tuple[str, ...]) -> chess.Board:
        cached = self._san_cache.get(moves)
        if cached is not None:
            return cached
        if moves:
            board = self._board_after(moves[:-1]).copy(stack=False)
            board.push_san(moves[-1])
        else:
            board = chess.Board()
        self._san_cache[moves] = board
        return board

    def _index(self) -> None:
        for line in self.curated_lines + self.lines:
            for i in range(len(line.moves)):
                before = self._board_after(line.moves[:i])
                self.book_moves.setdefault(_epd(before), set()).add(line.moves[i])
            final = _epd(self._board_after(line.moves))
            current = self.names_by_epd.get(final)
            if current is None or (current.curated is None and line.curated is None
                                   and len(line.name) > len(current.name)):
                self.names_by_epd[final] = line

    def _index_cache_path(self) -> Path:
        stamp = hashlib.sha1(f"{INDEX_VERSION}".encode())
        for path in sorted(DATA_DIR.glob("*.tsv")) + [Path(__file__).with_name("curated.py")]:
            stamp.update(path.read_bytes())
        return CACHE_DIR / f"book-{stamp.hexdigest()[:12]}.pkl"

    def _load_index_cache(self):
        try:
            names, moves = pickle.loads(self._index_cache_path().read_bytes())
        except (OSError, pickle.PickleError, ValueError, EOFError):
            return None
        lookup = {(line.name, line.moves): line for line in self.all_lines()}
        return {epd: lookup[key] for epd, key in names.items()}, moves

    def _save_index_cache(self) -> None:
        names = {epd: (line.name, line.moves) for epd, line in self.names_by_epd.items()}
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._index_cache_path().write_bytes(pickle.dumps((names, self.book_moves)))
        except OSError:
            pass

    # --------------------------------------------------------------- queries
    def all_lines(self) -> list[Line]:
        return self.curated_lines + self.lines

    def board_after(self, moves) -> chess.Board:
        return self._board_after(tuple(moves)).copy(stack=False)

    def name_of(self, board: chess.Board) -> Line | None:
        """The most specific named line for this exact position (transpositions count)."""
        return self.names_by_epd.get(_epd(board))

    def opening_at(self, moves: list[chess.Move]) -> Line | None:
        """The last named position reached while replaying these moves."""
        board = chess.Board()
        found = None
        for move in moves:
            board.push(move)
            found = self.name_of(board) or found
        return found

    def is_book(self, board: chess.Board, san: str) -> bool:
        return san in self.book_moves.get(_epd(board), ())

    def note(self, board: chess.Board, move: chess.Move) -> str | None:
        return self.notes.get((_epd(board), move.uci()))

    def mistake_note(self, board: chess.Board, move: chess.Move) -> str | None:
        return self.mistakes.get((_epd(board), move.uci()))

    def curated_for(self, line: Line) -> Curated | None:
        """The closest teaching notes: the same line, else same variation, else same family."""
        if line.curated:
            return line.curated
        best: Line | None = None
        for candidate in self.curated_lines:
            name = candidate.name
            if line.name.startswith(name) and (best is None or len(name) > len(best.name)):
                best = candidate
        if best:
            return best.curated
        for candidate in self.curated_lines:
            if candidate.family == line.family:
                return candidate.curated
        return None

    def search(self, query: str, curated_only: bool = False) -> list[Line]:
        lines = self.curated_lines if curated_only else self.all_lines()
        terms = query.lower().split()
        if not terms:
            return list(lines)
        def matches(line: Line) -> bool:
            hay = f"{line.eco} {line.name} {line.pretty}".lower()
            return all(term in hay for term in terms)
        return [line for line in lines if matches(line)]

    def group(self, line: Line, scope: str) -> list[Line]:
        """Lines drilled together: 'line', 'variation' or 'family'."""
        if scope == "line":
            return [line]
        if scope == "variation":
            prefix = line.variation
            found = [o for o in self.all_lines() if o.variation == prefix]
        else:
            found = [o for o in self.all_lines() if o.family == line.family]
        return found or [line]
