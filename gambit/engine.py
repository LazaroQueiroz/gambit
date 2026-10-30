"""Position evaluation: Stockfish when available, a small built-in search otherwise."""

from __future__ import annotations

import glob
import json
import math
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.engine

MATE_CP = 10000
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "gambit"


@dataclass
class PV:
    cp: int  # from White's point of view
    moves: list[chess.Move]
    mate: int | None = None  # moves until mate, positive when White mates


@dataclass
class Analysis:
    lines: list[PV] = field(default_factory=list)
    source: str = ""

    @property
    def best(self) -> PV | None:
        return self.lines[0] if self.lines else None

    @property
    def cp(self) -> int:
        return self.lines[0].cp if self.lines else 0


def win_percent(cp: int) -> float:
    """Lichess's centipawn to winning-chance curve, 0 to 100, from White's side."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def terminal_analysis(board: chess.Board) -> Analysis | None:
    if board.is_checkmate():
        cp = -MATE_CP if board.turn == chess.WHITE else MATE_CP
        return Analysis([PV(cp, [], 0)], "rules")
    if board.is_stalemate() or board.is_insufficient_material():
        return Analysis([PV(0, [])], "rules")
    return None


# ---------------------------------------------------------------- built-in
VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}
# Michniewski's simplified evaluation tables, written from White's side, a8 first.
_PST = {
    chess.PAWN: [0]*8 + [50]*8 + [10,10,20,30,30,20,10,10] + [5,5,10,25,25,10,5,5] + [0,0,0,20,20,0,0,0]
        + [5,-5,-10,0,0,-10,-5,5] + [5,10,10,-20,-20,10,10,5] + [0]*8,
    chess.KNIGHT: [-50,-40,-30,-30,-30,-30,-40,-50, -40,-20,0,0,0,0,-20,-40, -30,0,10,15,15,10,0,-30,
        -30,5,15,20,20,15,5,-30, -30,0,15,20,20,15,0,-30, -30,5,10,15,15,10,5,-30, -40,-20,0,5,5,0,-20,-40,
        -50,-40,-30,-30,-30,-30,-40,-50],
    chess.BISHOP: [-20,-10,-10,-10,-10,-10,-10,-20, -10,0,0,0,0,0,0,-10, -10,0,5,10,10,5,0,-10,
        -10,5,5,10,10,5,5,-10, -10,0,10,10,10,10,0,-10, -10,10,10,10,10,10,10,-10, -10,5,0,0,0,0,5,-10,
        -20,-10,-10,-10,-10,-10,-10,-20],
    chess.ROOK: [0]*8 + [5,10,10,10,10,10,10,5] + [-5,0,0,0,0,0,0,-5]*5 + [0,0,0,5,5,0,0,0],
    chess.QUEEN: [-20,-10,-10,-5,-5,-10,-10,-20, -10,0,0,0,0,0,0,-10, -10,0,5,5,5,5,0,-10, -5,0,5,5,5,5,0,-5,
        0,0,5,5,5,5,0,-5, -10,5,5,5,5,5,0,-10, -10,0,5,0,0,0,0,-10, -20,-10,-10,-5,-5,-10,-10,-20],
    chess.KING: [-30,-40,-40,-50,-50,-40,-40,-30]*4 + [-20,-30,-30,-40,-40,-30,-30,-20,
        -10,-20,-20,-20,-20,-20,-20,-10, 20,20,0,0,0,0,20,20, 20,30,10,0,0,10,30,20],
}


def _static_eval(board: chess.Board) -> int:
    """Score from the side to move's perspective."""
    score = 0
    for square, piece in board.piece_map().items():
        table = _PST[piece.piece_type]
        if piece.color == chess.WHITE:
            score += VALUES[piece.piece_type] + table[square ^ 56]
        else:
            score -= VALUES[piece.piece_type] + table[square]
    return score if board.turn == chess.WHITE else -score


class _Timeout(Exception):
    pass


class BuiltinEngine:
    """A depth-limited alpha-beta search. Sees short tactics, not positional subtleties."""

    label = "basic evaluator"
    reliable = False

    def __init__(self, seconds: float = 0.7) -> None:
        self.seconds = seconds

    def analyse(self, board: chess.Board, multipv: int = 3) -> Analysis:
        root = board.copy(stack=False)
        self._deadline = time.monotonic() + self.seconds
        ordered = self._ordered(root)
        results: list[tuple[int, list[chess.Move]]] = []
        for depth in range(1, 5):
            try:
                scored = []
                for move in ordered:
                    root.push(move)
                    try:
                        score, pv = self._search(root, depth - 1, -MATE_CP * 2, MATE_CP * 2)
                    finally:
                        root.pop()
                    scored.append((-score, [move] + pv))
            except _Timeout:
                break
            scored.sort(key=lambda item: -item[0])
            results = scored
            ordered = [item[1][0] for item in scored]
        if not results:  # not even depth 1 finished; use a static look
            for move in ordered:
                root.push(move)
                results.append((-_static_eval(root), [move]))
                root.pop()
            results.sort(key=lambda item: -item[0])
        sign = 1 if board.turn == chess.WHITE else -1
        lines = [PV(score * sign, pv, self._mate(score * sign)) for score, pv in results[:multipv]]
        return Analysis(lines, self.label)

    @staticmethod
    def _mate(score: int) -> int | None:
        if abs(score) < MATE_CP - 100:
            return None
        plies = MATE_CP - abs(score)
        return (plies + 1) // 2 * (1 if score > 0 else -1)

    @staticmethod
    def _ordered(board: chess.Board) -> list[chess.Move]:
        def key(move: chess.Move) -> int:
            if board.is_capture(move):
                victim = board.piece_type_at(move.to_square) or chess.PAWN
                return -VALUES[victim] * 10 + VALUES[board.piece_type_at(move.from_square)]
            return 0
        return sorted(board.legal_moves, key=key)

    def _search(self, board, depth, alpha, beta, ply=1):
        if time.monotonic() > self._deadline:
            raise _Timeout
        if board.is_checkmate():
            return -(MATE_CP - ply), []
        if board.is_stalemate() or board.is_insufficient_material() or board.is_repetition(2):
            return 0, []
        if depth <= 0:
            return self._quiesce(board, alpha, beta, 4), []
        best_pv: list[chess.Move] = []
        for move in self._ordered(board):
            board.push(move)
            try:
                score, pv = self._search(board, depth - 1, -beta, -alpha, ply + 1)
            finally:
                board.pop()
            score = -score
            if score >= beta:
                return beta, [move] + pv
            if score > alpha:
                alpha, best_pv = score, [move] + pv
        return alpha, best_pv

    def _quiesce(self, board, alpha, beta, left):
        stand = _static_eval(board)
        if stand >= beta:
            return beta
        alpha = max(alpha, stand)
        if left == 0:
            return alpha
        for move in self._ordered(board):
            if not board.is_capture(move):
                continue
            board.push(move)
            try:
                score = -self._quiesce(board, -beta, -alpha, left - 1)
            finally:
                board.pop()
            if score >= beta:
                return beta
            alpha = max(alpha, score)
        return alpha

    def quit(self) -> None:
        pass


# --------------------------------------------------------------- Stockfish
class UciEngine:
    label = "Stockfish"
    reliable = True

    def __init__(self, path: str, seconds: float = 0.5, depth: int = 18, threads: int = 2, hash_mb: int = 64) -> None:
        self.path = path
        self.limit = chess.engine.Limit(time=seconds, depth=depth)
        self.options = {"Threads": threads, "Hash": hash_mb}
        self._engine: chess.engine.SimpleEngine | None = None

    def _ensure(self) -> chess.engine.SimpleEngine:
        if self._engine is None:
            self._engine = chess.engine.SimpleEngine.popen_uci(self.path, timeout=20)
            usable = {k: v for k, v in self.options.items() if k in self._engine.options}
            self._engine.configure(usable)
            self.label = self._engine.id.get("name", "Stockfish")
        return self._engine

    def analyse(self, board: chess.Board, multipv: int = 3) -> Analysis:
        infos = self._ensure().analyse(board, self.limit, multipv=multipv)
        lines = []
        for info in infos:
            score = info["score"].white()
            lines.append(PV(score.score(mate_score=MATE_CP), list(info.get("pv", [])), score.mate()))
        return Analysis(lines, self.label)

    def quit(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError, TimeoutError):
                pass
            self._engine = None


def find_stockfish(explicit: str | None = None) -> str | None:
    """Look in: --engine, $GAMBIT_ENGINE, the project's engine folder, then PATH."""
    if explicit:
        return explicit
    env = os.environ.get("GAMBIT_ENGINE")
    if env:
        return env
    bundled = sorted(glob.glob(str(PROJECT_DIR / "engine/stockfish/stockfish-*")))
    for candidate in bundled:
        if os.access(candidate, os.X_OK) and not os.path.isdir(candidate):
            return candidate
    return shutil.which("stockfish")


class Evaluator:
    """Thread-safe front end with a persistent cache, so repeat positions cost no CPU."""

    def __init__(self, engine_path: str | None = None, seconds: float = 0.5, use_stockfish: bool = True,
                 cache_path: Path | None = None) -> None:
        path = find_stockfish(engine_path) if use_stockfish else None
        self.engine = UciEngine(path, seconds) if path else BuiltinEngine()
        self.fallback_reason = None if path else "Stockfish not found"
        self._lock = threading.Lock()
        self._memory: dict[str, Analysis] = {}
        self._db = None
        cache_path = cache_path or DATA_HOME / "analysis.sqlite"
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(cache_path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, data TEXT)")
        except (OSError, sqlite3.Error):
            self._db = None

    @property
    def reliable(self) -> bool:
        return self.engine.reliable

    @property
    def label(self) -> str:
        return self.engine.label

    def analyse(self, board: chess.Board) -> Analysis:
        done = terminal_analysis(board)
        if done:
            return done
        key = f"{type(self.engine).__name__}|{board.epd()}"
        with self._lock:
            cached = self._memory.get(key) or self._read(key)
            if cached:
                self._memory[key] = cached
                return cached
            try:
                result = self.engine.analyse(board)
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError, TimeoutError) as exc:
                # Stockfish crashed or could not start: carry on with the basic evaluator.
                self.engine.quit()
                self.fallback_reason = f"Stockfish failed ({exc})"
                self.engine = BuiltinEngine()
                result = self.engine.analyse(board)
            self._memory[key] = result
            self._write(key, result)
            return result

    def _read(self, key: str) -> Analysis | None:
        if not self._db:
            return None
        try:
            row = self._db.execute("SELECT data FROM cache WHERE key=?", (key,)).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        data = json.loads(row[0])
        lines = [PV(l["cp"], [chess.Move.from_uci(m) for m in l["moves"]], l["mate"]) for l in data["lines"]]
        return Analysis(lines, data["source"])

    def _write(self, key: str, analysis: Analysis) -> None:
        if not self._db:
            return
        data = {"source": analysis.source,
                "lines": [{"cp": l.cp, "mate": l.mate, "moves": [m.uci() for m in l.moves]} for l in analysis.lines]}
        try:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)", (key, json.dumps(data)))
            self._db.commit()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        with self._lock:
            self.engine.quit()
            if self._db:
                self._db.close()
                self._db = None
