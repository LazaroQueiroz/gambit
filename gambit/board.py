"""The chessboard and evaluation bar widgets."""

from __future__ import annotations

import chess
from rich.style import Style
from rich.text import Text
from textual.events import MouseDown, MouseUp
from textual.geometry import Size
from textual.message import Message
from textual.widget import Widget

from .engine import win_percent

# 7x6 pixel art, drawn with half-block characters so a cell is 7 columns by 3 rows.
SPRITES = {
    chess.PAWN: ["       ", "   #   ", "  ###  ", "   #   ", "  ###  ", " ##### "],
    chess.KNIGHT: ["  ##   ", " ####  ", "###### ", "## ### ", "  #### ", " ##### "],
    chess.BISHOP: ["   #   ", "  # #  ", "  ###  ", "   #   ", "  ###  ", " ##### "],
    chess.ROOK: [" # # # ", " ##### ", "  ###  ", "  ###  ", "  ###  ", " ##### "],
    chess.QUEEN: ["#  #  #", " # # # ", " ##### ", "  ###  ", "  ###  ", " ##### "],
    chess.KING: ["   #   ", "  ###  ", "   #   ", " ##### ", "  ###  ", " ##### "],
}
GLYPHS = {chess.PAWN: "♟", chess.KNIGHT: "♞", chess.BISHOP: "♝", chess.ROOK: "♜", chess.QUEEN: "♛", chess.KING: "♚"}

LIGHT, DARK = "#a9b1d0", "#5d648c"
LAST_LIGHT, LAST_DARK = "#c9b866", "#94843a"
SELECT = "#89b4fa"
HINT = "#a6e3a1"
ERROR_SQUARE = "#f38ba8"
TARGET_DOT = "#3b3f58"
WHITE_PIECE, BLACK_PIECE = "#fdfaf0", "#14141f"


class ChessBoard(Widget):
    """Click a piece then a square, or drag it. Posts `MoveAttempt` with the chosen squares."""

    can_focus = True
    DEFAULT_CSS = "ChessBoard { width: auto; height: auto; }"

    class MoveAttempt(Message):
        def __init__(self, source: chess.Square, target: chess.Square) -> None:
            super().__init__()
            self.source = source
            self.target = target

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.board = chess.Board()
        self.white_bottom = True
        self.compact = False
        self.selected: chess.Square | None = None
        self.cursor: chess.Square = chess.E2
        self.show_cursor = False
        self.hints: set[chess.Square] = set()
        self.bad: set[chess.Square] = set()
        self.locked = False

    # -------------------------------------------------------------- geometry
    @property
    def cell_w(self) -> int:
        return 3 if self.compact else 7

    @property
    def cell_h(self) -> int:
        return 1 if self.compact else 3

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return 2 + self.cell_w * 8

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return self.cell_h * 8 + 1

    def set_view(self, board: chess.Board, white_bottom: bool | None = None) -> None:
        self.board = board
        if white_bottom is not None:
            self.white_bottom = white_bottom
        self.refresh(layout=True)

    def clear_marks(self) -> None:
        self.selected = None
        self.hints = set()
        self.bad = set()
        self.refresh()

    def set_compact(self, compact: bool) -> None:
        if compact != self.compact:
            self.compact = compact
            self.refresh(layout=True)

    def square_at(self, x: int, y: int) -> chess.Square | None:
        x -= 2
        if x < 0 or y < 0:
            return None
        column, row = x // self.cell_w, y // self.cell_h
        if column > 7 or row > 7:
            return None
        if self.white_bottom:
            return chess.square(column, 7 - row)
        return chess.square(7 - column, row)

    # -----------------------------------------------------------------input
    def _targets(self) -> set[chess.Square]:
        if self.selected is None:
            return set()
        return {m.to_square for m in self.board.legal_moves if m.from_square == self.selected}

    def press(self, square: chess.Square) -> None:
        """A click or the Enter key on a square."""
        if self.locked:
            return
        if self.selected is not None and square in self._targets():
            source, self.selected = self.selected, None
            self.post_message(self.MoveAttempt(source, square))
        else:
            piece = self.board.piece_at(square)
            self.selected = square if piece and piece.color == self.board.turn and square != self.selected else None
        self.refresh()

    def on_mouse_down(self, event: MouseDown) -> None:
        square = self.square_at(event.x, event.y)
        if square is not None:
            self.show_cursor = False
            self.press(square)

    def on_mouse_up(self, event: MouseUp) -> None:
        square = self.square_at(event.x, event.y)
        if square is not None and self.selected is not None and square != self.selected:
            self.press(square)  # the end of a drag

    def move_cursor(self, dx: int, dy: int) -> None:
        if not self.white_bottom:
            dx, dy = -dx, -dy
        file_index = min(7, max(0, chess.square_file(self.cursor) + dx))
        rank_index = min(7, max(0, chess.square_rank(self.cursor) + dy))
        self.cursor = chess.square(file_index, rank_index)
        self.show_cursor = True
        self.refresh()

    # -------------------------------------------------------------- drawing
    def _square_color(self, square: chess.Square) -> str:
        light = bool(chess.BB_LIGHT_SQUARES & chess.BB_SQUARES[square])
        last = self.board.move_stack[-1] if self.board.move_stack else None
        if square == self.selected:
            return SELECT
        if square in self.bad:
            return ERROR_SQUARE
        if square in self.hints:
            return HINT
        if last and square in (last.from_square, last.to_square):
            return LAST_LIGHT if light else LAST_DARK
        return LIGHT if light else DARK

    def render(self) -> Text:
        return self._render_compact() if self.compact else self._render_full()

    def _order(self):
        files = range(8) if self.white_bottom else range(7, -1, -1)
        ranks = range(7, -1, -1) if self.white_bottom else range(8)
        return list(files), list(ranks)

    def _render_full(self) -> Text:
        text = Text(no_wrap=True)
        files, ranks = self._order()
        targets = self._targets()
        label_style = Style(color="#a6adc8")
        for rank_index in ranks:
            for row in range(3):
                text.append(f"{rank_index + 1} " if row == 1 else "  ", label_style)
                for file_index in files:
                    square = chess.square(file_index, rank_index)
                    self._draw_cell(text, square, row, square in targets)
                text.append("\n")
        text.append("  ")
        for file_index in files:
            text.append(f"   {chess.FILE_NAMES[file_index]}   ", label_style)
        return text

    def _draw_cell(self, text: Text, square: chess.Square, row: int, is_target: bool) -> None:
        background = self._square_color(square)
        piece = self.board.piece_at(square)
        at_cursor = self.show_cursor and square == self.cursor
        if piece:
            color = WHITE_PIECE if piece.color == chess.WHITE else BLACK_PIECE
            if is_target:
                background = ERROR_SQUARE
            art = SPRITES[piece.piece_type]
            for x in range(7):
                top = art[row * 2][x] == "#"
                bottom = art[row * 2 + 1][x] == "#"
                if top and bottom:
                    text.append("█", Style(color=color, bgcolor=background))
                elif top:
                    text.append("▀", Style(color=color, bgcolor=background))
                elif bottom:
                    text.append("▄", Style(color=color, bgcolor=background))
                elif at_cursor and (row in (0, 2)) and x in (0, 6):
                    text.append("▪", Style(color=SELECT, bgcolor=background))
                else:
                    text.append(" ", Style(bgcolor=background))
            return
        if is_target and row == 1:
            text.append("   ●   ", Style(color=TARGET_DOT, bgcolor=background))
        elif at_cursor and row in (0, 2):
            text.append("▪     ▪", Style(color=SELECT, bgcolor=background))
        else:
            text.append("       ", Style(bgcolor=background))

    def _render_compact(self) -> Text:
        text = Text(no_wrap=True)
        files, ranks = self._order()
        targets = self._targets()
        label_style = Style(color="#a6adc8")
        for rank_index in ranks:
            text.append(f"{rank_index + 1} ", label_style)
            for file_index in files:
                square = chess.square(file_index, rank_index)
                background = self._square_color(square)
                piece = self.board.piece_at(square)
                left, right = (">", "<") if self.show_cursor and square == self.cursor else (" ", " ")
                if piece:
                    color = WHITE_PIECE if piece.color == chess.WHITE else BLACK_PIECE
                    if square in targets:
                        background = ERROR_SQUARE
                    text.append(f"{left}{GLYPHS[piece.piece_type]}{right}", Style(color=color, bgcolor=background, bold=True))
                elif square in targets:
                    text.append(f"{left}●{right}", Style(color=TARGET_DOT, bgcolor=background))
                else:
                    text.append(f"{left} {right}", Style(color=SELECT, bgcolor=background))
            text.append("\n")
        text.append("  ")
        for file_index in files:
            text.append(f" {chess.FILE_NAMES[file_index]} ", label_style)
        return text


class EvalBar(Widget):
    """Vertical winning-chances bar. The side at the bottom of the board fills from below."""

    DEFAULT_CSS = "EvalBar { width: 3; height: 100%; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cp = 0
        self.white_bottom = True
        self.pending = False

    def set_eval(self, cp: int, white_bottom: bool, pending: bool = False) -> None:
        self.cp, self.white_bottom, self.pending = cp, white_bottom, pending
        self.refresh()

    def render(self) -> Text:
        height = max(1, self.size.height)
        white_share = win_percent(self.cp) / 100
        bottom_share = white_share if self.white_bottom else 1 - white_share
        bottom_color = WHITE_PIECE if self.white_bottom else BLACK_PIECE
        top_color = BLACK_PIECE if self.white_bottom else WHITE_PIECE
        filled = bottom_share * height * 2  # half-row resolution
        text = Text(no_wrap=True)
        for row in range(height):
            from_bottom = (height - 1 - row) * 2
            half_cells = max(0, min(2, filled - from_bottom))
            whole = round(half_cells)
            if whole >= 2:
                text.append("   ", Style(bgcolor=bottom_color))
            elif whole == 1:
                text.append("▄▄▄", Style(color=bottom_color, bgcolor=top_color))
            else:
                text.append("   ", Style(bgcolor=top_color))
            if row < height - 1:
                text.append("\n")
        return text
