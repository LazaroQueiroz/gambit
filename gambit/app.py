"""The Textual interface."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

import chess
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from .board import ChessBoard, EvalBar
from .book import Book, Line, pretty_moves
from .drill import FREE, LEARN, TEST, Drill, Outcome
from .engine import Evaluator
from .explain import VERDICT_LABEL, describe, describe_eval, pov, score_text
from .progress import Progress

VERDICT_STYLE = {
    "book": "bold #89b4fa", "best": "bold #a6e3a1", "good": "bold #a6e3a1",
    "inaccuracy": "bold #f9e2af", "mistake": "bold #fab387", "blunder": "bold #f38ba8",
}
HOTKEYS_HELP = """\
[b]Moving[/b]
  Mouse       click a piece then a square, or drag it
  Typing      Nf3, exd5, O-O, e2e4, then Enter (Esc clears)
  Arrows      move the cursor, Enter or Space selects

[b]While training[/b]
  i   hint (first the piece, then the move)
  s   show the answer and why it works
  u   take back your move and try again
  p   play on against the engine after a wrong move
  v   flip the board
  r   restart this drill
  n   next drill, when one is finished
  q   back to the previous screen
  ?   this help

[b]Verdicts[/b] compare winning chances before and after your move.
Book means a named line contains it. Inaccuracy, Mistake and Blunder
mean a loss of 5, 10 and 20 percent of winning chances.
"""


@dataclass
class DrillSpec:
    lines: list[Line]
    side: chess.Color
    mode: str
    title: str
    next_spec: Callable[[], "DrillSpec | None"] | None = None


def side_name(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark,enter", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(HOTKEYS_HELP, id="help-box")


class DrillScreen(Screen):
    """The training board, used for tests, guided learning and free analysis."""

    BINDINGS = [
        Binding("u", "undo", "Take back"),
        Binding("i", "hint", "Hint"),
        Binding("s", "reveal", "Show answer"),
        Binding("p", "play_on", "Play on"),
        Binding("v", "flip", "Flip"),
        Binding("r", "restart", "Restart"),
        Binding("n", "next", "Next"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "back", "Back"),
    ]
    MOVE_CHARS = set("abcdefghNBRQKO0x12345678+#=-")

    def __init__(self, spec: DrillSpec) -> None:
        super().__init__()
        self.spec = spec
        self.drill: Drill | None = None
        self.buffer = ""
        self.token = 0  # bumped to discard analysis results that belong to an old position
        self.awaiting_decision = False
        self.pending_feedback = None
        self.feed: list[Text] = []
        self.hint_level = 0
        self.saved = False
        self.white_bottom = spec.side == chess.WHITE

    def compose(self) -> ComposeResult:
        with Horizontal(id="drill-root"):
            with Horizontal(id="board-area"):
                yield EvalBar(id="eval-bar")
                yield ChessBoard(id="board")
            with Vertical(id="side-panel"):
                yield Static(id="title")
                yield Static(id="eval-text")
                with VerticalScroll(id="feed-scroll"):
                    yield Static(id="feed")
                yield Static(id="moves")
                yield Static(id="prompt")
        yield Footer()

    @property
    def evaluator(self) -> Evaluator:
        return self.app.evaluator

    @property
    def book(self) -> Book:
        return self.app.book

    @property
    def board_widget(self) -> ChessBoard:
        return self.query_one(ChessBoard)

    def on_mount(self) -> None:
        self._fit_board()
        self.start()

    def on_resize(self) -> None:
        self._fit_board()

    def _fit_board(self) -> None:
        size = self.app.size
        self.board_widget.set_compact(size.height < 29 or size.width < 100)

    # ---------------------------------------------------------------- setup
    def start(self) -> None:
        self.token += 1
        self.drill = Drill(self.book, self.spec.lines, self.spec.side, self.spec.mode)
        self.saved = False
        self.awaiting_decision = False
        self.pending_feedback = None
        self.buffer = ""
        self.hint_level = 0
        self.feed = []
        board = self.board_widget
        board.clear_marks()
        board.locked = False
        board.set_view(self.drill.board, self.white_bottom)
        self._say_intro()
        self.refresh_panels()
        self.request_eval()
        if self.spec.mode != FREE and not self.drill.is_user_turn():
            self.set_timer(0.5, self.opponent_move)
        else:
            self.prepare_turn()

    def _say_intro(self) -> None:
        drill = self.drill
        if drill.mode == FREE:
            self.add(Text("Free board: move either side. Every move gets evaluated and explained.", "italic"))
            return
        line = drill.lines[0]
        notes = self.book.curated_for(line)
        if notes and (drill.mode == LEARN or len(drill.lines) == 1):
            text = Text()
            text.append(notes.summary + "\n", "italic")
            if drill.mode == LEARN:
                text.append("White's plan: ", "bold")
                text.append(notes.white_plan + "\n")
                text.append("Black's plan: ", "bold")
                text.append(notes.black_plan + "\n")
            self.add(text)
        self.add(Text(f"You play {side_name(drill.side)}.", "bold"))

    # ------------------------------------------------------------- feedback
    def add(self, text: Text) -> None:
        self.feed.append(text)
        try:
            entries = [entry.copy() for entry in self.feed]
            for entry in entries:
                entry.rstrip()
            self.query_one("#feed", Static).update(Text("\n").join(entries))
            self.query_one("#feed-scroll", VerticalScroll).scroll_end(animate=False)
        except Exception:
            pass

    def refresh_panels(self) -> None:
        drill = self.drill
        reached = self.book.opening_at(list(drill.board.move_stack))
        title = Text()
        if drill.mode == FREE:
            title.append("Free board\n", "bold #cba6f7")
            title.append(f"{reached.eco} {reached.name}" if reached else "Starting position", "#bac2de")
        else:
            label = {TEST: "Test", LEARN: "Learn"}[drill.mode]
            title.append(f"{label}: {self.spec.title}\n", "bold #cba6f7")
            title.append(f"{reached.eco} {reached.name}" if reached else "Starting position", "#bac2de")
            if drill.mode == TEST:
                title.append(f"\nScore {drill.right}/{drill.total}", "#a6e3a1")
                if drill.hints:
                    title.append(f"  hints {drill.hints}", "#f9e2af")
        self.query_one("#title", Static).update(title)
        self.query_one("#moves", Static).update(Text(pretty_moves(drill.sans) or "-", "#a6adc8"))
        self._update_prompt()

    def _update_prompt(self) -> None:
        drill = self.drill
        prompt = Text()
        if self.buffer:
            prompt.append(f"Move: {self.buffer}_  ", "bold #89b4fa")
        if drill.finished:
            prompt.append("n next   r again   q back", "bold #a6e3a1")
        elif self.awaiting_decision:
            prompt.append("u try again   s show answer   p play on", "bold #fab387")
        elif drill.is_user_turn() and not self.buffer:
            prompt.append(f"Your move ({side_name(drill.board.turn)})", "#bac2de")
        self.query_one("#prompt", Static).update(prompt)

    def _show_eval(self, cp: int, mate, viewer: chess.Color) -> None:
        own = pov(cp, viewer)
        text = Text()
        text.append(score_text(own, mate), "bold")
        text.append(f"  {describe_eval(own, mate)}\n", "#bac2de")
        source = self.evaluator.label
        style = "#6c7086"
        if not self.evaluator.reliable:
            source += " (run setup-engine.sh for Stockfish)"
            style = "#f9e2af"
        text.append(source, style)
        self.query_one("#eval-text", Static).update(text)
        self.query_one(EvalBar).set_eval(cp, self.white_bottom)

    def viewer(self) -> chess.Color:
        return self.drill.side if self.drill.mode != FREE else self.drill.board.turn

    def request_eval(self) -> None:
        self._eval_position(self.drill.board.copy(), self.token)

    @work(thread=True, group="eval", exit_on_error=False)
    def _eval_position(self, board: chess.Board, token: int) -> None:
        analysis = self.evaluator.analyse(board)
        if token == self.token and analysis.best:
            self.app.call_from_thread(self._apply_eval, analysis)

    def _apply_eval(self, analysis) -> None:
        self._show_eval(analysis.cp, analysis.best.mate, self.viewer())

    # ------------------------------------------------------------ user moves
    @on(ChessBoard.MoveAttempt)
    def from_board(self, event: ChessBoard.MoveAttempt) -> None:
        move = chess.Move(event.source, event.target)
        board = self.drill.board
        if move not in board.legal_moves:
            promote = chess.Move(event.source, event.target, promotion=chess.QUEEN)
            if promote not in board.legal_moves:
                return
            move = promote
        self.play_user_move(move)

    def play_user_move(self, move: chess.Move) -> None:
        drill = self.drill
        if drill.finished or self.awaiting_decision or not drill.is_user_turn():
            return
        before = drill.board.copy()
        self.token += 1
        self.hint_level = 0
        widget = self.board_widget
        widget.clear_marks()
        widget.locked = True
        drill.push(move)
        widget.set_view(drill.board)
        self.refresh_panels()
        thinking = Text(f"Analysing {drill.sans[-1]}...", "#6c7086")
        self.add(thinking)
        self._assess_move(before, move, self.token, thinking)

    @work(thread=True, group="assess", exit_on_error=False)
    def _assess_move(self, before_board: chess.Board, move: chess.Move, token: int, thinking: Text) -> None:
        before = self.evaluator.analyse(before_board)
        after_board = before_board.copy()
        after_board.push(move)
        after = self.evaluator.analyse(after_board)
        if token != self.token:
            return
        outcome = self.drill.judge(before_board, move, before, after, self.evaluator.reliable)
        self.app.call_from_thread(self._on_outcome, outcome, after, before_board.turn, thinking)

    def _on_outcome(self, outcome: Outcome, after, mover: chess.Color, thinking: Text) -> None:
        if thinking in self.feed:
            self.feed.remove(thinking)
        widget = self.board_widget
        drill = self.drill
        reveal = outcome.kind != "wrong" or drill.mode != TEST
        self.add(self._feedback_text(outcome, reveal))
        self._show_eval(after.cp, after.best.mate if after.best else None, self.viewer())
        widget.locked = False
        if outcome.kind == "correct":
            if outcome.line_over:
                self.finish()
            else:
                self.refresh_panels()
                widget.locked = True
                self.set_timer(0.6, self.opponent_move)
        elif outcome.kind in ("wrong", "alternative"):
            self.pending_feedback = outcome.feedback
            self.awaiting_decision = True
            widget.locked = True
            last = drill.board.peek()
            if outcome.kind == "wrong":
                widget.bad = {last.to_square}
                widget.refresh()
            self.refresh_panels()
        else:  # free play
            self.refresh_panels()
            if drill.mode != FREE and drill.off_book:
                widget.locked = True
                self.set_timer(0.4, self.engine_reply)
            else:
                self.prepare_turn()

    def _feedback_text(self, outcome: Outcome, reveal: bool) -> Text:
        fb = outcome.feedback
        drill = self.drill
        label, mark = VERDICT_LABEL[fb.verdict]
        text = Text()
        text.append("\n")
        text.append(f"{mark} {fb.san}: {label}", VERDICT_STYLE[fb.verdict])
        text.append(f"   {score_text(fb.cp_before)} → {score_text(fb.cp_after)}\n", "#a6adc8")
        if outcome.kind == "alternative":
            named = self.book.name_of(drill.board)
            where = f" ({named.name})" if named else ""
            text.append(f"A real book move{where}, but this drill follows a different line. Try again, no penalty.\n", "#89dceb")
        if fb.in_book and fb.verdict in ("inaccuracy", "mistake", "blunder"):
            text.append("It is in the book, but the engine dislikes it: gambits and old lines can be objectively dubious.\n", "#f9e2af")
        if fb.mistake_note:
            text.append(fb.mistake_note + "\n", "#fab387")
        if fb.book_note:
            text.append(fb.book_note + "\n")
        if not fb.book_note and not fb.mistake_note and fb.does:
            text.append(" ".join(fb.does) + "\n")
        elif fb.book_note and fb.does and fb.verdict not in ("book", "best", "good"):
            text.append(" ".join(fb.does) + "\n", "#bac2de")
        for problem in fb.problems:
            text.append(f"• {problem}\n", "#fab387" if fb.verdict in ("mistake", "blunder") else "#f9e2af")
        if fb.refutation:
            text.append(fb.refutation + "\n", "#f38ba8")
        if reveal and fb.better and fb.verdict in ("inaccuracy", "mistake", "blunder"):
            text.append(f"Better: {fb.better}\n", "#a6e3a1")
        return text

    def prepare_turn(self) -> None:
        drill = self.drill
        if drill.mode == LEARN and not drill.off_book and drill.is_user_turn():
            self._show_learn_hint()
        self.refresh_panels()

    def _show_learn_hint(self) -> None:
        drill = self.drill
        options = drill.expected()
        if not options:
            return
        move = drill.board.parse_san(options[0])
        widget = self.board_widget
        widget.hints = {move.from_square, move.to_square}
        widget.refresh()
        text = Text()
        text.append(f"\nPlay {options[0]}", "bold #a6e3a1")
        if len(options) > 1:
            text.append(f" (also in book: {', '.join(options[1:4])})", "#a6adc8")
        text.append("\n")
        note = self.book.note(drill.board, move)
        reasons = note or " ".join(describe(drill.board, move))
        if reasons:
            text.append(reasons + "\n")
        self.add(text)

    # ------------------------------------------------------------- opponent
    def opponent_move(self) -> None:
        drill = self.drill
        if drill.finished or drill.is_user_turn():
            return
        move = drill.choose_reply()
        if move is None:
            self.finish()
            return
        board_before = drill.board.copy()
        self._announce_reply(board_before, move)
        drill.push(move)
        widget = self.board_widget
        widget.clear_marks()
        widget.set_view(drill.board)
        widget.locked = False
        self.refresh_panels()
        self.token += 1
        self.request_eval()
        if drill.line_complete():
            self.finish()
        else:
            self.prepare_turn()

    def _announce_reply(self, board: chess.Board, move: chess.Move) -> None:
        san = board.san(move)
        number = f"{board.fullmove_number}{'.' if board.turn == chess.WHITE else '...'}"
        text = Text()
        text.append(f"\nOpponent: {number}{san}\n", "bold #cba6f7")
        note = self.book.note(board, move) or " ".join(describe(board, move)[:2])
        if note:
            text.append(note + "\n", "#bac2de")
        self.add(text)

    def engine_reply(self) -> None:
        self._engine_reply_worker(self.drill.board.copy(), self.token)

    @work(thread=True, group="assess", exit_on_error=False)
    def _engine_reply_worker(self, board: chess.Board, token: int) -> None:
        analysis = self.evaluator.analyse(board)
        if token != self.token or not analysis.best or not analysis.best.moves:
            if token == self.token:
                self.app.call_from_thread(self._no_reply)
            return
        self.app.call_from_thread(self._apply_engine_reply, analysis.best.moves[0])

    def _no_reply(self) -> None:
        self.board_widget.locked = False
        self.add(Text("\nThe game is over.", "bold"))

    def _apply_engine_reply(self, move: chess.Move) -> None:
        drill = self.drill
        self._announce_reply(drill.board.copy(), move)
        drill.push(move)
        widget = self.board_widget
        widget.clear_marks()
        widget.set_view(drill.board)
        widget.locked = False
        self.refresh_panels()
        self.token += 1
        self.request_eval()

    # --------------------------------------------------------------- finish
    def finish(self) -> None:
        drill = self.drill
        drill.finished = True
        self.board_widget.locked = True
        self.token += 1
        result = drill.result()
        text = Text()
        text.append("\n── Line complete ──\n", "bold #a6e3a1")
        if drill.mode == TEST:
            text.append(f"First-try accuracy: {result.right}/{result.total} ({result.percent}%)\n", "bold")
            if result.hints:
                text.append(f"Hints used: {result.hints}\n")
            if result.total:
                text.append(f"Average change per move: {result.cp_loss / result.total / 100:.2f} pawns lost\n")
            if result.errors:
                text.append("Wrong tries: " + ", ".join(result.errors) + "\n", "#fab387")
            self._save_result(result)
        notes = self.book.curated_for(drill.reached) if drill.reached else None
        if notes:
            plan = notes.white_plan if drill.side == chess.WHITE else notes.black_plan
            text.append("Your plan from here: ", "bold")
            text.append(plan + "\n")
        self.add(text)
        self.refresh_panels()
        self.request_eval_final()

    def request_eval_final(self) -> None:
        self._eval_position(self.drill.board.copy(), self.token)

    def _save_result(self, result) -> None:
        if self.saved or not self.drill.reached or not result.total:
            return
        self.saved = True
        line = self.drill.reached
        self.app.progress.record_drill(line.key, line.name, side_name(self.drill.side).lower(), result.right, result.total)

    def abandon_scored(self) -> None:
        """Playing on abandons the line: it still counts, as an imperfect attempt."""
        drill = self.drill
        if drill.mode == TEST and not self.saved and drill.reached:
            self.saved = True
            line = drill.reached
            self.app.progress.record_drill(line.key, line.name, side_name(drill.side).lower(),
                                           drill.right, max(drill.total + 1, 1))

    # -------------------------------------------------------------- actions
    def action_undo(self) -> None:
        drill = self.drill
        if drill.finished:
            return
        widget = self.board_widget
        if self.awaiting_decision:
            drill.undo()
            self.awaiting_decision = False
            self.pending_feedback = None
        elif drill.mode == FREE or drill.off_book:
            if not drill.board.move_stack:
                return
            drill.undo()
            if drill.mode != FREE and drill.board.move_stack:
                drill.undo()
        else:
            return
        self.token += 1
        widget.clear_marks()
        widget.locked = False
        widget.set_view(drill.board)
        self.request_eval()
        self.prepare_turn()

    def action_hint(self) -> None:
        drill = self.drill
        options = drill.expected()
        if drill.mode != TEST or not options or self.awaiting_decision or not drill.is_user_turn():
            return
        drill.use_hint()
        move = drill.board.parse_san(options[0])
        widget = self.board_widget
        self.hint_level += 1
        widget.hints = {move.from_square} if self.hint_level == 1 else {move.from_square, move.to_square}
        widget.refresh()
        self.refresh_panels()

    def action_reveal(self) -> None:
        drill = self.drill
        if drill.finished or drill.mode == FREE or drill.off_book:
            return
        fb = self.pending_feedback
        if self.awaiting_decision:
            wanted = self._expected_before_last()
            text = Text()
            text.append(f"\nThe line continues {', '.join(wanted[:4])}.\n", "bold #a6e3a1")
            board = drill.board.copy()
            board.pop()
            move = board.parse_san(wanted[0])
            why = self.book.note(board, move) or " ".join(describe(board, move))
            if why:
                text.append(why + "\n")
            if fb and fb.better:
                text.append(f"Engine's choice: {fb.better}\n", "#bac2de")
            self.add(text)
            return
        options = drill.expected()
        if not options or not drill.is_user_turn():
            return
        drill.use_hint()
        move = drill.board.parse_san(options[0])
        widget = self.board_widget
        widget.hints = {move.from_square, move.to_square}
        widget.refresh()
        why = self.book.note(drill.board, move) or " ".join(describe(drill.board, move))
        text = Text()
        text.append(f"\nAnswer: {options[0]}\n", "bold #a6e3a1")
        if why:
            text.append(why + "\n")
        self.add(text)
        self.refresh_panels()

    def _expected_before_last(self) -> list[str]:
        drill = self.drill
        depth = len(drill.sans) - 1
        prefix = tuple(drill.sans[:depth])
        found = []
        for line in drill.lines:
            if len(line.moves) > depth and line.moves[:depth] == prefix and line.moves[depth] not in found:
                found.append(line.moves[depth])
        return found

    def action_play_on(self) -> None:
        drill = self.drill
        if not self.awaiting_decision or drill.mode == FREE:
            return
        self.abandon_scored()
        drill.leave_book()
        self.awaiting_decision = False
        widget = self.board_widget
        widget.bad = set()
        widget.refresh()
        self.add(Text("\nOff the book: the engine now plays its best replies against you.", "italic"))
        self.refresh_panels()
        widget.locked = True
        self.set_timer(0.3, self.engine_reply)

    def action_flip(self) -> None:
        self.white_bottom = not self.white_bottom
        widget = self.board_widget
        widget.set_view(self.drill.board, self.white_bottom)
        bar = self.query_one(EvalBar)
        bar.set_eval(bar.cp, self.white_bottom)

    def action_restart(self) -> None:
        self.start()

    def action_next(self) -> None:
        if not self.drill.finished:
            return
        if not self.spec.next_spec:
            self.start()
            return
        nxt = self.spec.next_spec()
        if nxt is None:
            self.add(Text("\nNothing else is due. Well done!", "bold #a6e3a1"))
            return
        self.spec = nxt
        self.white_bottom = nxt.side == chess.WHITE
        self.start()

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_back(self) -> None:
        self.token += 1
        self.app.pop_screen()

    # ----------------------------------------------------------- keyboard
    def on_key(self, event) -> None:
        key = event.key
        char = event.character
        arrows = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}
        widget = self.board_widget
        if key in arrows:
            widget.move_cursor(*arrows[key])
            event.stop()
        elif key in ("enter", "space"):
            if self.buffer:
                self._submit_buffer()
            elif widget.show_cursor:
                widget.press(widget.cursor)
            event.stop()
        elif key == "backspace":
            self.buffer = self.buffer[:-1]
            self._update_prompt()
            event.stop()
        elif key == "escape":
            if self.buffer or widget.selected is not None:
                self.buffer = ""
                widget.selected = None
                widget.refresh()
                self._update_prompt()
            else:
                self.action_back()
            event.stop()
        elif char and char in self.MOVE_CHARS:
            self.buffer += char
            self._update_prompt()
            event.stop()

    def _submit_buffer(self) -> None:
        text, self.buffer = self.buffer.strip(), ""
        board = self.drill.board
        move = None
        for parse in (lambda: board.parse_san(text.replace("0", "O")), lambda: board.parse_san(text), lambda: board.parse_uci(text)):
            try:
                move = parse()
                break
            except ValueError:
                continue
        if move is None or not self.drill.is_user_turn() or self.board_widget.locked:
            self.add(Text(f"\nCould not play “{text}”. Use capital piece letters, like Nf3.", "#f38ba8"))
            self._update_prompt()
            return
        self.play_user_move(move)


class StartModal(ModalScreen):
    """Choose how to train the selected opening."""

    BINDINGS = [Binding("escape,q", "dismiss(None)", "Cancel"), Binding("tab", "swap", "Swap side")]

    def __init__(self, line: Line, book: Book) -> None:
        super().__init__()
        self.line = line
        self.book = book
        self.side = line.default_side

    def compose(self) -> ComposeResult:
        with Vertical(id="start-box"):
            yield Static(id="start-title")
            yield OptionList(id="start-options")
            yield Static("Enter chooses · Tab swaps side · Esc cancels", id="start-help")

    def on_mount(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        title = Text()
        title.append(f"{self.line.eco} {self.line.name}\n", "bold #cba6f7")
        title.append(f"You play: {side_name(self.side)}", "bold")
        self.query_one("#start-title", Static).update(title)
        options = self.query_one("#start-options", OptionList)
        highlighted = options.highlighted
        options.clear_options()
        variation = len(self.book.group(self.line, "variation"))
        family = len(self.book.group(self.line, "family"))
        options.add_option(Option("Learn this line (guided, with explanations)", id="learn:line"))
        options.add_option(Option("Test this line", id="test:line"))
        if variation > 1:
            options.add_option(Option(f"Test the whole variation ({variation} lines)", id="test:variation"))
        if family > 1:
            options.add_option(Option(f"Test the whole {self.line.family} family ({family} lines)", id="test:family"))
        options.highlighted = highlighted if highlighted is not None else 0
        options.focus()

    def action_swap(self) -> None:
        self.side = not self.side
        self._rebuild()

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        mode, scope = event.option.id.split(":")
        self.dismiss((mode, scope, self.side))


class BrowseScreen(Screen):
    BINDINGS = [
        Binding("ctrl+t", "toggle_curated", "With lessons only"),
        Binding("escape", "back", "Back"),
        Binding("down", "focus_list", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.curated_only = True
        self.results: list[Line] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search by name, ECO code or moves: najdorf, B90, Nf3 Nc6 ...", id="search")
        with Horizontal(id="browse-root"):
            yield OptionList(id="results")
            with VerticalScroll(id="details"):
                yield Static(id="detail-text")
        yield Footer()

    def on_mount(self) -> None:
        self.apply_search("")
        self.query_one(Input).focus()

    @on(Input.Changed)
    def changed(self, event: Input.Changed) -> None:
        self.apply_search(event.value)

    def apply_search(self, query: str) -> None:
        self.results = self.app.book.search(query, self.curated_only)
        options = self.query_one("#results", OptionList)
        options.clear_options()
        rows = []
        for i, line in enumerate(self.results[:600]):
            star = "★ " if line.curated else "  "
            rows.append(Option(f"{star}{line.eco}  {line.name}", id=str(i)))
        options.add_options(rows)
        if rows:
            options.highlighted = 0
        else:
            self.query_one("#detail-text", Static).update(
                Text("Nothing found. Press Ctrl+T to search all 3,800+ lines." if self.curated_only else "Nothing found."))
        scope = "with lessons" if self.curated_only else "all lines"
        shown = f"{len(self.results)} found, showing 600" if len(self.results) > 600 else f"{len(self.results)} found"
        self.query_one("#results", OptionList).border_title = f"{scope}: {shown}"

    def action_toggle_curated(self) -> None:
        self.curated_only = not self.curated_only
        self.apply_search(self.query_one(Input).value)

    def action_focus_list(self) -> None:
        self.query_one("#results", OptionList).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(OptionList.OptionHighlighted, "#results")
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        line = self.results[int(event.option.id)]
        text = Text()
        text.append(f"{line.eco}  {line.name}\n\n", "bold #cba6f7")
        text.append(line.pretty + "\n\n", "#a6adc8")
        notes = self.app.book.curated_for(line)
        if notes:
            if not line.curated:
                text.append(f"From the lesson on {notes.name}:\n", "italic #6c7086")
            text.append(notes.summary + "\n\n")
            text.append("White's plan\n", "bold")
            text.append(notes.white_plan + "\n\n")
            text.append("Black's plan\n", "bold")
            text.append(notes.black_plan + "\n\n")
        text.append("Enter to train", "#89b4fa")
        self.query_one("#detail-text", Static).update(text)

    @on(Input.Submitted)
    def from_input(self) -> None:
        options = self.query_one("#results", OptionList)
        if self.results:
            options.focus()
            options.action_select()

    @on(OptionList.OptionSelected, "#results")
    def selected(self, event: OptionList.OptionSelected) -> None:
        line = self.results[int(event.option.id)]
        def begin(choice) -> None:
            if not choice:
                return
            mode, scope, side = choice
            lines = self.app.book.group(line, scope)
            title = {"line": line.name, "variation": line.variation, "family": line.family}[scope]
            self.app.open_drill(DrillSpec(lines, side, mode, title))
        self.app.push_screen(StartModal(line, self.app.book), begin)


class QuizScreen(Screen):
    """Name that opening: see the position, pick the right name."""

    BINDINGS = [Binding("q,escape", "back", "Back"), Binding("n", "next", "Next")]

    def __init__(self) -> None:
        super().__init__()
        self.answer: Line | None = None
        self.right = self.total = 0
        self.answered = False
        self.rng = random.Random()

    def compose(self) -> ComposeResult:
        with Horizontal(id="drill-root"):
            with Horizontal(id="board-area"):
                yield ChessBoard(id="board")
            with Vertical(id="side-panel"):
                yield Static(id="quiz-title")
                yield OptionList(id="quiz-options")
                with VerticalScroll(id="feed-scroll"):
                    yield Static(id="quiz-feedback")
        yield Footer()

    def on_mount(self) -> None:
        size = self.app.size
        self.query_one(ChessBoard).set_compact(size.height < 29 or size.width < 100)
        self.next_question()

    def next_question(self) -> None:
        book = self.app.book
        self.answered = False
        self.answer = self.rng.choice(book.curated_lines)
        same_start = [l for l in book.curated_lines if l.family != self.answer.family
                      and l.moves[:1] == self.answer.moves[:1]]
        other = [l for l in book.curated_lines if l.family != self.answer.family]
        pool = list({l.family: l for l in same_start}.values())
        self.rng.shuffle(pool)
        rest = list({l.family: l for l in other}.values())
        self.rng.shuffle(rest)
        choices: list[Line] = [self.answer]
        for candidate in pool + rest:
            if len(choices) == 4:
                break
            if candidate.family not in {c.family for c in choices}:
                choices.append(candidate)
        self.rng.shuffle(choices)
        self.choices = choices
        depth = min(len(self.answer.moves), self.rng.choice((6, 8, 10)))
        board = chess.Board()
        for san in self.answer.moves[:depth]:
            board.push_san(san)
        widget = self.query_one(ChessBoard)
        widget.locked = True
        widget.set_view(board, True)
        title = Text()
        title.append("Name that opening\n", "bold #cba6f7")
        title.append(f"Score {self.right}/{self.total}\n", "#a6e3a1")
        title.append(pretty_moves(self.answer.moves[:depth]), "#a6adc8")
        self.query_one("#quiz-title", Static).update(title)
        options = self.query_one("#quiz-options", OptionList)
        options.clear_options()
        options.add_options([Option(choice.family, id=str(i)) for i, choice in enumerate(choices)])
        options.highlighted = 0
        options.focus()
        self.query_one("#quiz-feedback", Static).update("")

    @on(OptionList.OptionSelected, "#quiz-options")
    def picked(self, event: OptionList.OptionSelected) -> None:
        if self.answered:
            self.next_question()
            return
        self.answered = True
        choice = self.choices[int(event.option.id)]
        correct = choice.family == self.answer.family
        self.total += 1
        self.right += int(correct)
        self.app.progress.record_quiz(correct)
        text = Text()
        text.append("Correct!\n" if correct else f"It is {self.answer.family}.\n",
                    "bold #a6e3a1" if correct else "bold #f38ba8")
        text.append(f"{self.answer.eco} {self.answer.name}\n")
        if self.answer.curated:
            text.append(self.answer.curated.summary + "\n")
        text.append("\nPress Enter or n for the next one.", "#89b4fa")
        self.query_one("#quiz-feedback", Static).update(text)

    def action_next(self) -> None:
        self.next_question()

    def action_back(self) -> None:
        self.app.pop_screen()


class StatsScreen(Screen):
    BINDINGS = [Binding("q,escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="stats-root"):
            yield Static(id="stats")
        yield Footer()

    def on_mount(self) -> None:
        stats = self.app.progress.summary()
        text = Text()
        text.append("Your progress\n\n", "bold #cba6f7")
        accuracy = f"{round(100 * stats['right'] / stats['total'])}%" if stats["total"] else "-"
        for label, value in (("Lines studied", stats["studied"]), ("Mastered", stats["mastered"]),
                             ("Due for review", stats["due"]), ("First-try accuracy", accuracy),
                             ("Day streak", stats["streak"]),
                             ("Name-that-opening", f"{stats['quiz']['right']}/{stats['quiz']['total']}")):
            text.append(f"  {label:<22}", "#bac2de")
            text.append(f"{value}\n", "bold")
        if stats["families"]:
            text.append("\nBy opening family\n", "bold #cba6f7")
            rows = sorted(stats["families"].items(), key=lambda kv: -kv[1][1])
            for family, (right, total) in rows[:20]:
                share = right / total if total else 0
                bar = "█" * round(share * 20)
                text.append(f"  {family[:30]:<31}", "#bac2de")
                text.append(f"{bar:<20}", "#a6e3a1" if share >= 0.8 else "#f9e2af" if share >= 0.5 else "#f38ba8")
                text.append(f" {round(share * 100)}%\n")
        if stats["weak"]:
            text.append("\nWeakest lines\n", "bold #cba6f7")
            for entry in stats["weak"]:
                text.append(f"  {round(100 * entry['right'] / entry['total']):>3}%  {entry['name']}\n")
        if not stats["studied"]:
            text.append("\nNothing yet: finish a test and it will show up here.\n", "italic #6c7086")
        self.query_one("#stats", Static).update(text)

    def action_back(self) -> None:
        self.app.pop_screen()


class MenuScreen(Screen):
    BINDINGS = [Binding("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-box"):
            yield Static(id="logo")
            yield OptionList(id="menu")
            yield Static(id="engine-note")
        yield Footer()

    def on_mount(self) -> None:
        self.rebuild()

    def on_screen_resume(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        app = self.app
        logo = Text()
        logo.append("♞ gambit\n", "bold #cba6f7")
        logo.append(f"{len(app.book.lines):,} named lines · {len(app.book.curated_lines)} with full lessons", "#a6adc8")
        self.query_one("#logo", Static).update(logo)
        due = len(app.progress.due())
        menu = self.query_one("#menu", OptionList)
        keep = menu.highlighted
        menu.clear_options()
        menu.add_options([
            Option("Browse openings: learn or test any line", id="browse"),
            Option("Random test: any opening with a lesson", id="random"),
            Option("Random test: the full book, including rare lines", id="random-all"),
            Option(f"Review due lines ({due})", id="review", disabled=due == 0),
            Option("Free board: play any moves and get them explained", id="free"),
            Option("Name that opening: quiz", id="quiz"),
            Option("Your progress", id="stats"),
        ])
        menu.highlighted = keep if keep is not None else 0
        menu.focus()
        note = Text()
        if app.evaluator.reliable:
            note.append(f"Evaluation: {app.evaluator.label}", "#6c7086")
        else:
            note.append("Evaluation: basic built-in search, which only sees short tactics.\n", "#f9e2af")
            note.append("Run ~/gambit/setup-engine.sh to enable Stockfish.", "#f9e2af")
        self.query_one("#engine-note", Static).update(note)

    @on(OptionList.OptionSelected, "#menu")
    def selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        app = self.app
        if action == "browse":
            app.push_screen(BrowseScreen())
        elif action == "random":
            app.open_drill(app.random_spec(app.book.curated_lines))
        elif action == "random-all":
            app.open_drill(app.random_spec(app.book.all_lines()))
        elif action == "review":
            spec = app.review_spec()
            if spec:
                app.open_drill(spec)
        elif action == "free":
            app.open_drill(DrillSpec([], chess.WHITE, FREE, "Free board"))
        elif action == "quiz":
            app.push_screen(QuizScreen())
        elif action == "stats":
            app.push_screen(StatsScreen())


class GambitApp(App):
    TITLE = "gambit"
    CSS = """
    Screen { background: $surface; }
    #menu-box { align: center middle; width: 100%; height: 1fr; padding: 2 4; }
    #logo { width: 64; margin-bottom: 1; }
    #menu { width: 64; height: auto; max-height: 12; }
    #engine-note { width: 64; margin-top: 1; }
    #drill-root { height: 1fr; }
    #board-area { width: auto; height: auto; padding: 1 1 0 1; }
    #eval-bar { margin-right: 1; }
    #side-panel { width: 1fr; height: 100%; padding: 1 2 0 1; }
    #title { height: auto; margin-bottom: 1; }
    #eval-text { height: auto; margin-bottom: 1; }
    #feed-scroll { height: 1fr; scrollbar-size-vertical: 1; }
    #moves { height: auto; max-height: 4; margin-top: 1; }
    #prompt { height: 1; }
    #search { dock: top; }
    #browse-root { height: 1fr; }
    #results { width: 1fr; height: 100%; border: round $primary; }
    #details { width: 1fr; height: 100%; padding: 1 2; }
    #start-box { width: 64; height: auto; padding: 1 2; background: $panel; border: round $primary; }
    StartModal { align: center middle; }
    #start-title { margin-bottom: 1; }
    #start-options { height: auto; max-height: 8; }
    #start-help { margin-top: 1; color: $text-muted; }
    HelpScreen { align: center middle; }
    #help-box { width: 74; height: auto; padding: 1 2; background: $panel; border: round $primary; }
    #quiz-title { height: auto; margin-bottom: 1; }
    #quiz-options { height: auto; max-height: 8; margin-bottom: 1; }
    #stats-root { padding: 1 3; }
    """

    def __init__(self, book: Book, evaluator: Evaluator, progress: Progress, start: DrillSpec | None = None) -> None:
        super().__init__()
        self.book = book
        self.evaluator = evaluator
        self.progress = progress
        self.start_spec = start
        self.rng = random.Random()

    def on_mount(self) -> None:
        self.theme = "catppuccin-mocha"
        self.push_screen(MenuScreen())
        if self.start_spec:
            self.open_drill(self.start_spec)

    def open_drill(self, spec: DrillSpec | None) -> None:
        if spec:
            self.push_screen(DrillScreen(spec))

    def random_spec(self, pool: list[Line]) -> DrillSpec:
        line = self.rng.choice(pool)
        return DrillSpec([line], line.default_side, TEST, line.name, lambda: self.random_spec(pool))

    def review_spec(self) -> DrillSpec | None:
        due = self.progress.due()
        by_key = {line.key: line for line in self.book.all_lines()}
        for key, entry in due:
            line = by_key.get(key)
            if line:
                side = chess.WHITE if entry["side"] == "white" else chess.BLACK
                return DrillSpec([line], side, TEST, f"Review: {line.name}", self.review_spec)
        return None

    def on_unmount(self) -> None:
        self.evaluator.close()
