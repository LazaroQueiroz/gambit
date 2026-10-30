"""Turns a move plus engine numbers into a plain-language explanation.

Everything said here is computed from the actual position (attacks, exchanges,
pawn structure, the engine's line), so it stays honest on any of the book's lines
and on moves that were never annotated by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from .engine import Analysis, MATE_CP, win_percent

PIECE_NAME = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
              chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king"}
POINTS = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
CENTER = {chess.E4, chess.D4, chess.E5, chess.D5}
EXT_CENTER = chess.SquareSet(chess.BB_CENTER) | chess.SquareSet(0x00003C3C3C3C0000)


@dataclass
class Feedback:
    san: str
    verdict: str  # book, best, good, inaccuracy, mistake, blunder
    in_book: bool
    cp_before: int  # from the mover's point of view
    cp_after: int
    win_drop: float
    best_san: str | None
    book_note: str | None = None
    mistake_note: str | None = None
    does: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    refutation: str | None = None
    better: str | None = None  # withheld from the user until they ask, in tests


VERDICT_LABEL = {
    "book": ("Book move", "✓"), "best": ("Best move", "★"), "good": ("Good move", "✓"),
    "inaccuracy": ("Inaccuracy", "?!"), "mistake": ("Mistake", "?"), "blunder": ("Blunder", "??"),
}


def see_after(board: chess.Board, square: chess.Square) -> int:
    """What the side to move wins by starting an exchange on `square`, in pawns."""
    attackers = [m for m in board.legal_moves if m.to_square == square and board.is_capture(m)
                 and not board.is_en_passant(m)]
    if not attackers:
        return 0
    move = min(attackers, key=lambda m: POINTS[board.piece_type_at(m.from_square)] or 100)
    captured = POINTS[board.piece_type_at(square)]
    board.push(move)
    net = captured - see_after(board, square)
    board.pop()
    return max(0, net)


def hanging_pieces(board: chess.Board, owner: chess.Color) -> list[tuple[chess.Square, int]]:
    """`owner`'s pieces the side to move can win outright. Requires owner not to move."""
    if board.turn == owner:
        return []
    found = []
    for square, piece in board.piece_map().items():
        if piece.color == owner and piece.piece_type != chess.KING:
            gain = see_after(board, square)
            if gain > 0:
                found.append((square, gain))
    return found


def material(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for piece in board.piece_map().values():
        total += POINTS[piece.piece_type] * (1 if piece.color == color else -1)
    return total


def _doubled(board, color, file_index) -> bool:
    return len(board.pieces(chess.PAWN, color) & chess.SquareSet(chess.BB_FILES[file_index])) >= 2


def _isolated(board, color, file_index) -> bool:
    pawns = board.pieces(chess.PAWN, color)
    for side in (file_index - 1, file_index + 1):
        if 0 <= side <= 7 and pawns & chess.SquareSet(chess.BB_FILES[side]):
            return False
    return bool(pawns & chess.SquareSet(chess.BB_FILES[file_index]))


def _new_targets(before: chess.Board, after: chess.Board, move: chess.Move, color) -> list[str]:
    """Enemy pieces newly attacked by the moved piece that it could profitably take."""
    out = []
    piece = after.piece_at(move.to_square)
    for square in after.attacks(move.to_square):
        target = after.piece_at(square)
        if not target or target.color == color or target.piece_type == chess.KING:
            continue
        if before.is_attacked_by(color, square) and move.from_square in before.attackers(color, square):
            continue
        defended = after.is_attacked_by(not color, square)
        if POINTS[target.piece_type] > POINTS[piece.piece_type] or not defended:
            out.append(f"the {PIECE_NAME[target.piece_type]} on {chess.square_name(square)}")
    return out


def describe(board: chess.Board, move: chess.Move) -> list[str]:
    """What a move concretely does. `board` is the position before it."""
    color = board.turn
    piece = board.piece_at(move.from_square)
    after = board.copy(stack=False)
    is_capture = board.is_capture(move)
    captured = board.piece_at(move.to_square)
    gives_check = board.gives_check(move)
    after.push(move)
    facts: list[str] = []
    kind = piece.piece_type
    name = PIECE_NAME[kind]
    to = chess.square_name(move.to_square)

    if board.is_castling(move):
        facts.append("Castles: the king reaches safety and the rook joins the game.")
    elif is_capture:
        victim = PIECE_NAME[captured.piece_type] if captured else "pawn"
        facts.append(f"Captures the {victim} on {to}.")
    if gives_check:
        facts.append("Gives check, so the reply is forced to deal with it.")

    home = chess.BB_RANK_1 if color == chess.WHITE else chess.BB_RANK_8
    if kind in (chess.KNIGHT, chess.BISHOP) and chess.BB_SQUARES[move.from_square] & home:
        facts.append(f"Develops the {name} off the back rank.")
    if kind == chess.PAWN:
        if move.to_square in CENTER:
            facts.append(f"Puts a pawn in the centre on {to}, where it controls key squares.")
        elif move.to_square in (chess.C4, chess.C5, chess.F4, chess.F5):
            facts.append(f"Fights for the centre from the flank with a pawn on {to}.")
        hits = [s for s in after.attacks(move.to_square) if (p := after.piece_at(s))
                and p.color != color and p.piece_type == chess.PAWN and s in EXT_CENTER]
        if hits and not is_capture:
            facts.append(f"Challenges the pawn on {chess.square_name(hits[0])}.")
        opened = _opened_line_for(board, after, color)
        if opened:
            facts.append(opened)
        if chess.square_file(move.to_square) in (1, 6) and abs(move.to_square - move.from_square) == 8:
            bishop = "b2" if color == chess.WHITE else "b7"
            if chess.square_file(move.to_square) == 6:
                bishop = "g2" if color == chess.WHITE else "g7"
            facts.append(f"Prepares a fianchetto: the bishop can go to {bishop} and watch the long diagonal.")
    if kind == chess.BISHOP and to in ("g2", "b2", "g7", "b7"):
        facts.append("Fianchettoes the bishop onto the long diagonal.")
    if kind in (chess.KNIGHT, chess.BISHOP, chess.QUEEN, chess.ROOK) and not is_capture:
        central = chess.SquareSet(after.attacks(move.to_square)) & chess.SquareSet(chess.BB_CENTER)
        if len(central) >= 2:
            facts.append(f"The {name} now eyes the central squares {_names(central)}.")
    targets = _new_targets(board, after, move, color)
    if gives_check and targets and kind != chess.PAWN:
        facts.append(f"Attacks two things at once: the king and {targets[0]}.")
    elif len(targets) >= 2:
        facts.append(f"Attacks two things at once: {targets[0]} and {targets[1]}.")
    elif targets:
        facts.append(f"Attacks {targets[0]}.")
    pinned = _new_pin(board, after, move, color)
    if pinned:
        facts.append(pinned)
    if kind == chess.ROOK and not (after.pieces(chess.PAWN, color) & chess.SquareSet(chess.BB_FILES[chess.square_file(move.to_square)])):
        facts.append(f"Places the rook on a file with none of your own pawns in the way.")
    return facts


def _names(squares) -> str:
    names = [chess.square_name(s) for s in squares]
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]


def _opened_line_for(before, after, color) -> str | None:
    gained: list[str] = []
    for kind, name in ((chess.BISHOP, "bishop"), (chess.QUEEN, "queen")):
        for square in after.pieces(kind, color):
            if len(after.attacks(square)) >= len(before.attacks(square)) + 2:
                gained.append(name)
    if not gained:
        return None
    unique = list(dict.fromkeys(gained))
    return f"Opens lines for your {' and '.join(unique)}."


def _new_pin(before, after, move, color) -> str | None:
    enemy = not color
    for square, piece in after.piece_map().items():
        if piece.color != enemy or piece.piece_type == chess.KING:
            continue
        if after.is_pinned(enemy, square) and not before.is_pinned(enemy, square):
            return f"Pins the {PIECE_NAME[piece.piece_type]} on {chess.square_name(square)} against the king."
    return None


def drawbacks(board: chess.Board, move: chess.Move) -> list[str]:
    """Positional and tactical costs of a move, regardless of the engine's verdict."""
    color = board.turn
    piece = board.piece_at(move.from_square)
    kind = piece.piece_type
    after = board.copy(stack=False)
    after.push(move)
    notes: list[str] = []
    early = board.fullmove_number <= 10

    for square, gain in hanging_pieces(after, color):
        target = after.piece_at(square)
        word = "the piece you just moved" if square == move.to_square else f"your {PIECE_NAME[target.piece_type]}"
        where = chess.square_name(square)
        notes.append(f"Leaves {word} on {where} loose: the opponent can win {gain} point{'s' if gain != 1 else ''} of material there.")

    if not early:
        return notes
    rank_from = chess.square_rank(move.from_square)
    home_rank = 0 if color == chess.WHITE else 7
    if kind == chess.QUEEN and sum(1 for sq, p in board.piece_map().items()
                                   if p.color == color and p.piece_type in (chess.KNIGHT, chess.BISHOP)
                                   and chess.square_rank(sq) == home_rank) >= 2:
        notes.append("Brings the queen out early: it can be chased by minor pieces, each attack gaining the opponent a free move.")
    if kind in (chess.KNIGHT, chess.BISHOP) and rank_from != home_rank and not board.is_capture(move):
        undeveloped = sum(1 for sq, p in board.piece_map().items() if p.color == color
                          and p.piece_type in (chess.KNIGHT, chess.BISHOP) and chess.square_rank(sq) == home_rank)
        attacked = board.is_attacked_by(not color, move.from_square)
        if undeveloped >= 1 and not attacked:
            notes.append("Moves the same piece twice while other pieces are still undeveloped.")
    if kind == chess.KNIGHT and chess.square_file(move.to_square) in (0, 7):
        notes.append("A knight on the rim controls few squares, so it does less from here.")
    if kind == chess.KING and not board.is_castling(move) and board.has_castling_rights(color):
        notes.append("Gives up the right to castle, leaving the king more exposed.")
    if kind == chess.ROOK and board.has_castling_rights(color) and rank_from == home_rank:
        side = "kingside" if move.from_square in (chess.H1, chess.H8) else "queenside"
        if (side == "kingside" and board.has_kingside_castling_rights(color)) or (side == "queenside" and board.has_queenside_castling_rights(color)):
            notes.append(f"Gives up {side} castling.")
    if kind == chess.PAWN:
        file_index = chess.square_file(move.to_square)
        if chess.square_file(move.from_square) in (5,) and after.king(color) in (chess.E1, chess.E8):
            notes.append("Moves the f-pawn with the king still in the centre, which loosens the e1-h4 / e8-h5 diagonal.")
        if _doubled(after, color, file_index) and not _doubled(board, color, file_index):
            notes.append("Creates doubled pawns, which are harder to protect and control fewer squares.")
        if _isolated(after, color, file_index) and not _isolated(board, color, file_index):
            notes.append("Leaves an isolated pawn, which no other pawn can ever protect.")
        if file_index in (0, 7) and board.fullmove_number <= 5 and not board.is_capture(move):
            notes.append("A rook-pawn move this early does little for the centre or development.")
    if kind in (chess.KNIGHT, chess.BISHOP):
        blocked = _blocks_center_pawn(after, move.to_square, color)
        if blocked:
            notes.append(blocked)
    return notes


def _blocks_center_pawn(board, square, color) -> str | None:
    ahead = {chess.WHITE: {chess.D3: chess.D2, chess.E3: chess.E2},
             chess.BLACK: {chess.D6: chess.D7, chess.E6: chess.E7}}[color]
    pawn_square = ahead.get(square)
    if pawn_square and board.piece_type_at(pawn_square) == chess.PAWN:
        return f"Blocks the pawn on {chess.square_name(pawn_square)}, which makes your own development harder."
    return None


def pov(cp: int, color: chess.Color) -> int:
    return cp if color == chess.WHITE else -cp


def verdict_for(drop: float, is_best: bool, reliable: bool) -> str:
    """Win-chance loss thresholds (Lichess's), relaxed when the evaluator is weak."""
    scale = 1.0 if reliable else 1.6
    if is_best:
        return "best"
    if drop >= 20 * scale:
        return "blunder"
    if drop >= 10 * scale:
        return "mistake"
    if drop >= 5 * scale:
        return "inaccuracy"
    return "good"


def format_line(board: chess.Board, moves: list[chess.Move], limit: int = 6) -> str:
    probe = board.copy(stack=False)
    parts: list[str] = []
    for i, move in enumerate(moves[:limit]):
        if move not in probe.legal_moves:
            break
        if probe.turn == chess.WHITE:
            parts.append(f"{probe.fullmove_number}.{probe.san(move)}")
        elif i == 0:
            parts.append(f"{probe.fullmove_number}...{probe.san(move)}")
        else:
            parts.append(probe.san(move))
        probe.push(move)
    return " ".join(parts)


def _material_swing(board: chess.Board, moves: list[chess.Move], color: chess.Color, plies: int = 8) -> int:
    """Material change for `color` along an engine line, stopping once captures settle."""
    probe = board.copy(stack=False)
    start = material(probe, color)
    last = start
    for move in moves[:plies]:
        if move not in probe.legal_moves:
            break
        probe.push(move)
        last = material(probe, color)
    return last - start


def describe_eval(cp: int, mate: int | None = None) -> str:
    """Plain-language assessment, cp from the viewer's side."""
    if mate is not None and abs(cp) >= MATE_CP - 1000:
        if mate == 0:
            return "checkmate on the board" if cp < 0 else "you delivered checkmate"
        return f"you can force mate in {abs(mate)}" if cp > 0 else f"you get mated in {abs(mate)}"
    size = abs(cp)
    if size <= 25:
        return "roughly equal"
    side = "you are" if cp > 0 else "your opponent is"
    if size <= 75:
        return f"{side} slightly better"
    if size <= 150:
        return f"{side} clearly better"
    if size <= 400:
        return f"{side} much better"
    return f"{side} winning"


def score_text(cp: int, mate: int | None = None) -> str:
    if abs(cp) >= MATE_CP - 1000:
        moves = str(abs(mate)) if mate else ""
        return f"M{moves}" if cp > 0 else f"-M{moves}"
    return f"{cp / 100:+.2f}"


def assess(book, board: chess.Board, move: chess.Move, before: Analysis, after: Analysis,
           reliable: bool = True) -> Feedback:
    """Full judgement of `move`, played from `board`. All scores are from the mover's side."""
    color = board.turn
    san = board.san(move)
    after_board = board.copy(stack=False)
    after_board.push(move)
    cp_before = pov(before.cp, color)
    cp_after = pov(after.cp, color)
    best = before.best
    best_move = best.moves[0] if best and best.moves else None
    best_san = board.san(best_move) if best_move else None
    drop = max(0.0, win_percent(cp_before) - win_percent(cp_after))
    is_best = best_move == move
    in_book = book.is_book(board, san)
    verdict = verdict_for(drop, is_best, reliable)
    feedback = Feedback(san, verdict, in_book, cp_before, cp_after, drop, best_san,
                        book.note(board, move), book.mistake_note(board, move))
    if in_book and verdict in ("best", "good", "inaccuracy"):
        feedback.verdict = "book"
    feedback.does = describe(board, move)
    feedback.problems = drawbacks(board, move)
    bad = verdict in ("inaccuracy", "mistake", "blunder")
    # A "hanging" piece the engine doesn't punish is a real gambit or a tactic, not a blunder.
    if not bad:
        feedback.problems = [p for p in feedback.problems if "loose" not in p]
        if any(b_gain for _, b_gain in hanging_pieces(after_board, color)):
            feedback.does.append("It offers material, but the engine sees enough compensation.")
    reply = after.best
    if bad and reply and reply.moves:
        line = format_line(after_board, reply.moves)
        swing = _material_swing(after_board, reply.moves, color)
        text = f"Likely punishment: {line}."
        if swing <= -1:
            text += f" That line costs you {-swing} point{'s' if swing != -1 else ''} of material."
        elif verdict in ("mistake", "blunder"):
            text += " No material is lost right away, but your position gets worse."
        feedback.refutation = text
    if best_move and not is_best:
        reasons = describe(board, best_move)
        note = book.note(board, best_move)
        lead = f"{best_san} ({score_text(pov(best.cp, color), best.mate)})"
        pieces = [note] if note else reasons[:3]
        feedback.better = f"{lead}: {' '.join(pieces)}" if pieces else lead
    return feedback
