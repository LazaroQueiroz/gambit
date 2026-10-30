import os
import stat
import sys
from datetime import date, timedelta
from pathlib import Path

import chess
import pytest

from gambit.app import BrowseScreen, DrillScreen, DrillSpec, GambitApp, MenuScreen, QuizScreen, StatsScreen
from gambit.book import Book, parse_annotated
from gambit.board import ChessBoard
from gambit.curated import OPENINGS
from gambit.drill import FREE, LEARN, TEST, Drill
from gambit.engine import BuiltinEngine, Evaluator, UciEngine, Analysis, PV, win_percent
from gambit.explain import assess, describe, drawbacks, hanging_pieces, see_after
from gambit.progress import Progress


@pytest.fixture(scope="session")
def book():
    return Book()


@pytest.fixture
def evaluator(tmp_path):
    ev = Evaluator(use_stockfish=False, cache_path=tmp_path / "c.sqlite")
    ev.engine.seconds = 0.05
    yield ev
    ev.close()


def make_app(book, evaluator, tmp_path, start=None):
    return GambitApp(book, evaluator, Progress(tmp_path / "p.json"), start)


def board_of(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


def fake(cp, move=None):
    return Analysis([PV(cp, [move] if move else [])], "t")


# ------------------------------------------------------------------- the book
def test_book_is_large_and_curated_lines_are_legal(book):
    assert len(book.lines) > 3500
    assert len(book.curated_lines) >= 80
    assert len(book.notes) > 500


def test_curated_names_match_the_dataset(book):
    names = [line.name for line in book.lines]
    for opening in OPENINGS:
        assert any(name.startswith(opening.name) for name in names), opening.name


def test_curated_sides_are_valid():
    assert {o.side for o in OPENINGS} <= {"white", "black"}


def test_mistakes_are_for_the_right_side_and_not_the_main_line(book):
    for opening in OPENINGS:
        for before, san, why in opening.mistakes:
            assert why
            board = board_of(*before.split())
            board.parse_san(san)


def test_parse_annotated():
    assert parse_annotated("1.e4 {a b} e5 2.Nf3") == [("e4", "a b"), ("e5", None), ("Nf3", None)]


def test_transposition_gets_the_name(book):
    # The Sicilian Najdorf position reached by a different move order keeps its name.
    a = board_of("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6")
    b = board_of("Nf3", "c5", "e4", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6")
    assert "Najdorf" in book.name_of(a).name
    assert book.name_of(b).name == book.name_of(a).name


def test_search_matches_name_eco_and_moves(book):
    assert any("Najdorf" in l.name for l in book.search("najdorf"))
    assert all(l.eco == "B90" or "B90" in l.name for l in book.search("B90"))
    assert book.search("najdorf", curated_only=True)[0].curated


def test_curated_for_finds_nearest_lesson(book):
    line = next(l for l in book.lines if l.name.startswith("Sicilian Defense: Najdorf Variation, English Attack"))
    assert book.curated_for(line).name == "Sicilian Defense: Najdorf Variation"


def test_default_side(book):
    assert next(l for l in book.lines if l.name.startswith("French Defense")).default_side == chess.BLACK
    assert next(l for l in book.lines if l.name.startswith("Vienna Game")).default_side == chess.WHITE


# ---------------------------------------------------------------- explanations
def test_see_counts_exchanges():
    board = chess.Board("4k3/8/8/3p4/8/2N5/8/4K3 w - - 0 1")
    assert see_after(board, chess.D5) == 1  # undefended pawn
    board = chess.Board("4k3/8/4p3/3p4/8/2N5/8/4K3 w - - 0 1")
    assert see_after(board, chess.D5) == 0  # knight for pawn loses, so White won't start


def test_hanging_piece_detected():
    assert not hanging_pieces(board_of("e4", "e5", "Qg4"), chess.WHITE)  # d7 still blocks the bishop
    assert (chess.G4, 9) in hanging_pieces(board_of("e4", "d5", "Qg4"), chess.WHITE)


def test_describe_development_and_centre():
    assert any("Develops" in f for f in describe(chess.Board(), chess.Move.from_uci("g1f3")))
    assert any("centre" in f for f in describe(chess.Board(), chess.Move.from_uci("e2e4")))
    board = board_of("e4", "e5", "Nf3", "Nc6", "Nc3")
    assert any("Castles" in f for f in describe(board_of("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"), chess.Move.from_uci("e1g1")))
    assert board.turn == chess.BLACK


def test_describe_pin_and_fork():
    board = board_of("e4", "e5", "Nf3", "Nc6", "d4", "d6")
    move = board.parse_san("Bb5")
    assert any("Pins the knight" in f for f in describe(board, move))
    fork = chess.Board("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1")
    assert any("two things" in f for f in describe(fork, fork.parse_san("Nc7")))


def test_drawbacks_list():
    start = chess.Board()
    assert any("f-pawn" in n for n in drawbacks(start, start.parse_san("f3")))
    assert any("rim" in n for n in drawbacks(start, start.parse_san("Na3")))
    board = board_of("e4", "e5")
    assert any("queen out" in n for n in drawbacks(board, board.parse_san("Qh5")))
    board = board_of("e4", "e5", "Nf3", "Nc6")
    assert any("same piece twice" in n for n in drawbacks(board, board.parse_san("Ng5")))
    board = board_of("e4", "d5")
    assert any("loose" in n for n in drawbacks(board, board.parse_san("Qg4")))


def test_assess_marks_book_and_blunder(book):
    board = chess.Board()
    move = board.parse_san("e4")
    fb = assess(book, board, move, fake(20, move), fake(25))
    assert fb.verdict == "book" and fb.in_book and fb.book_note

    board = board_of("e4", "e5", "Nf3", "Nc6")
    board = board_of("e4", "e5")
    bad = board.parse_san("Qg4")
    reply = board_of("e4", "e5", "Qg4").parse_san("d5")
    fb = assess(book, board, bad, fake(20, board.parse_san("Nf3")), fake(-600, reply))
    assert fb.verdict == "blunder"
    assert fb.refutation and "d5" in fb.refutation
    assert fb.better and "Nf3" in fb.better


def test_gambit_is_not_called_a_blunder_when_engine_agrees(book):
    board = board_of("e4", "e5")
    move = board.parse_san("f4")
    fb = assess(book, board, move, fake(20, move), fake(15))
    assert fb.verdict == "book"
    assert not any("loose" in p for p in fb.problems)


def test_mover_viewpoint_for_black(book):
    board = board_of("e4")
    move = board.parse_san("c5")
    fb = assess(book, board, move, fake(30, move), fake(30))
    assert fb.cp_before == -30


# ------------------------------------------------------------------- engines
def test_builtin_engine_finds_mate_and_material():
    engine = BuiltinEngine(seconds=0.5)
    mate = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
    result = engine.analyse(mate)
    assert result.best.moves[0] == mate.parse_san("Rd8#")
    assert result.best.mate == 1
    free_queen = chess.Board("4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1")
    assert engine.analyse(free_queen).best.moves[0] == free_queen.parse_san("exd5")


def test_uci_integration_with_fake_engine(tmp_path):
    script = tmp_path / "engine"
    script.write_text(f"#!/bin/sh\nexec {sys.executable} {Path(__file__).parent / 'fake_uci.py'}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    ev = Evaluator(str(script), seconds=0.1, cache_path=tmp_path / "c.sqlite")
    try:
        analysis = ev.analyse(chess.Board())
        assert len(analysis.lines) == 3
        assert analysis.best.cp == 20
        assert ev.label == "FakeFish"
        assert ev.reliable
    finally:
        ev.close()


def test_missing_engine_falls_back(tmp_path):
    ev = Evaluator("/nonexistent/stockfish", cache_path=tmp_path / "c.sqlite")
    try:
        assert ev.analyse(chess.Board()).best
        assert not ev.reliable
        assert "failed" in ev.fallback_reason
    finally:
        ev.close()


def test_evaluator_cache_survives_restart(tmp_path):
    path = tmp_path / "c.sqlite"
    first = Evaluator(use_stockfish=False, cache_path=path)
    first.engine.seconds = 0.05
    result = first.analyse(chess.Board())
    first.close()
    second = Evaluator(use_stockfish=False, cache_path=path)
    second.engine.analyse = lambda *a: pytest.fail("should have used the cache")
    assert second.analyse(chess.Board()).cp == result.cp
    second.close()


def test_terminal_positions_have_no_engine_call(evaluator):
    board = board_of("f3", "e5", "g4", "Qh4#")
    analysis = evaluator.analyse(board)
    assert analysis.cp < -9000 and analysis.best.mate == 0


def test_win_percent_is_monotonic():
    assert win_percent(-500) < win_percent(0) == 50 < win_percent(500)


# ------------------------------------------------------------------ drill rules
def _drill(book, name, side, mode=TEST):
    line = next(l for l in book.curated_lines if l.name == name)
    return Drill(book, [line], side, mode), line


def _judge(drill, san, cp=20):
    board = drill.board.copy()
    move = board.parse_san(san)
    drill.push(move)
    return drill.judge(board, move, fake(cp, move), fake(cp))


def test_correct_moves_score_and_finish(book):
    drill, line = _drill(book, "Scotch Game", chess.WHITE)
    for i, san in enumerate(line.moves):
        if drill.board.turn == chess.WHITE:
            outcome = _judge(drill, san)
            assert outcome.kind == "correct"
        else:
            assert drill.choose_reply() == drill.board.parse_san(san)
            drill.push(drill.choose_reply())
    assert drill.line_complete()
    assert drill.right == drill.total > 0


def test_wrong_move_counts_only_once_and_blocks_the_score(book):
    drill, line = _drill(book, "Scotch Game", chess.WHITE)
    _judge(drill, "e4")
    drill.push(drill.choose_reply())
    for _ in range(2):
        board = drill.board.copy()
        bad = board.parse_san("Ba6")
        drill.push(bad)
        outcome = drill.judge(board, bad, fake(20, board.parse_san("Nf3")), fake(-300))
        assert outcome.kind == "wrong"
        drill.undo()
    assert _judge(drill, "Nf3").kind == "correct"
    assert drill.total == 2 and drill.right == 1 and len(drill.errors) == 1


def test_other_book_move_is_alternative(book):
    drill, _ = _drill(book, "Scotch Game", chess.WHITE)
    outcome = _judge(drill, "d4")
    assert outcome.kind == "alternative"
    assert drill.total == 0


def test_family_drill_branches(book):
    lines = book.group(next(l for l in book.curated_lines if l.name == "Scotch Game"), "family")
    drill = Drill(book, lines, chess.BLACK)
    assert len(lines) > 3
    drill.push(chess.Board().parse_san("e4"))
    assert drill.expected()


def test_hint_loses_the_point(book):
    drill, _ = _drill(book, "Scotch Game", chess.WHITE)
    drill.use_hint()
    _judge(drill, "e4")
    assert drill.right == 0 and drill.total == 1 and drill.hints == 1


def test_free_mode_has_no_book(book):
    drill = Drill(book, [], chess.WHITE, FREE)
    assert drill.is_user_turn()
    drill.push(chess.Board().parse_san("a4"))
    assert drill.is_user_turn()


# -------------------------------------------------------------------- progress
def test_spaced_repetition(tmp_path):
    progress = Progress(tmp_path / "p.json")
    today = date(2026, 1, 1)
    progress.record_drill("e4", "Test: Line", "white", 3, 3, today)
    entry = progress.data["lines"]["e4"]
    assert entry["box"] == 1 and entry["due"] == (today + timedelta(days=1)).isoformat()
    assert not progress.due(today)
    assert progress.due(today + timedelta(days=1))
    progress.record_drill("e4", "Test: Line", "white", 1, 3, today)
    assert progress.data["lines"]["e4"]["box"] == 0
    assert Progress(tmp_path / "p.json").data["lines"]["e4"]["attempts"] == 2


def test_streak_and_summary(tmp_path):
    progress = Progress(tmp_path / "p.json")
    today = date.today()
    progress.record_drill("a", "Fam: A", "white", 1, 2, today - timedelta(days=1))
    progress.record_drill("b", "Fam: B", "black", 2, 2, today)
    summary = progress.summary()
    assert summary["streak"] == 2 and summary["families"]["Fam"] == [3, 4]


def test_corrupt_progress_file_is_ignored(tmp_path):
    (tmp_path / "p.json").write_text("{nope")
    assert Progress(tmp_path / "p.json").data["lines"] == {}


# ------------------------------------------------------------------ whole app
async def _settle(pilot, screen, predicate, tries=120):
    for _ in range(tries):
        await pilot.pause(0.05)
        if predicate():
            return True
    return False


def _spec(book, name="Scotch Game", side=chess.WHITE, mode=TEST):
    line = next(l for l in book.curated_lines if l.name == name)
    return DrillSpec([line], side, mode, name), line


@pytest.mark.asyncio
async def test_menu_and_all_screens_open(book, evaluator, tmp_path):
    app = make_app(book, evaluator, tmp_path)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, BrowseScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("down", "down", "down", "down", "enter")  # disabled review is skipped by the list
        await pilot.pause()
        assert not isinstance(app.screen, MenuScreen)
        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        await app.push_screen(StatsScreen())
        await pilot.pause()
        await pilot.press("q")
        await app.push_screen(QuizScreen())
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.total == 1
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.answered is False


@pytest.mark.asyncio
async def test_full_test_by_typing_then_saved(book, evaluator, tmp_path):
    spec, line = _spec(book)
    app = make_app(book, evaluator, tmp_path, spec)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, DrillScreen)
        for ply in range(0, len(line.moves)):
            assert await _settle(pilot, screen, lambda: len(screen.drill.sans) >= ply and not screen.board_widget.locked or screen.drill.finished)
            if screen.drill.finished:
                break
            if ply % 2 == 0 and screen.drill.board.turn == chess.WHITE and len(screen.drill.sans) == ply:
                await pilot.press(*list(line.moves[ply]), "enter")
        assert await _settle(pilot, screen, lambda: screen.drill.finished)
        assert screen.drill.right == screen.drill.total > 0
        saved = app.progress.data["lines"][line.key]
        assert saved["clean"] == 1 and saved["box"] == 1
        assert "Line complete" in " ".join(t.plain for t in screen.feed)


@pytest.mark.asyncio
async def test_wrong_move_then_retry_and_reveal(book, evaluator, tmp_path):
    spec, line = _spec(book)
    app = make_app(book, evaluator, tmp_path, spec)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("e", "4", "enter")
        assert await _settle(pilot, screen, lambda: len(screen.drill.sans) == 2 and not screen.board_widget.locked)
        await pilot.press(*"Ba6", "enter")  # hangs the bishop and is in no book
        assert await _settle(pilot, screen, lambda: screen.awaiting_decision)
        spoiled = screen.feed[-1].plain
        assert "Better:" not in spoiled and "Nf3" not in spoiled.split("punishment")[0]
        assert "loose" in spoiled
        await pilot.press("s")
        await pilot.pause()
        assert "line continues Nf3" in screen.feed[-1].plain
        await pilot.press("u")
        await pilot.pause()
        assert screen.drill.sans == ["e4", "e5"] and not screen.awaiting_decision
        await pilot.press(*"Nf3", "enter")
        assert await _settle(pilot, screen, lambda: len(screen.drill.sans) == 4)
        assert screen.drill.right == 1 and screen.drill.total == 2


@pytest.mark.asyncio
async def test_play_on_against_engine(book, evaluator, tmp_path):
    spec, _ = _spec(book)
    app = make_app(book, evaluator, tmp_path, spec)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("a", "3", "enter")
        assert await _settle(pilot, screen, lambda: screen.awaiting_decision)
        await pilot.press("p")
        assert await _settle(pilot, screen, lambda: len(screen.drill.sans) == 2)
        assert screen.drill.off_book
        await pilot.press("e", "4", "enter")
        assert await _settle(pilot, screen, lambda: len(screen.drill.sans) == 4)
        await pilot.press("u")
        await pilot.pause()
        assert len(screen.drill.sans) == 2
        assert app.progress.data["lines"]  # an abandoned test still counts


@pytest.mark.asyncio
async def test_black_side_opponent_moves_first_and_mouse_works(book, evaluator, tmp_path):
    spec, line = _spec(book, "Petrov's Defense", chess.BLACK)
    app = make_app(book, evaluator, tmp_path, spec)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert await _settle(pilot, screen, lambda: screen.drill.sans == ["e4"])
        widget = screen.board_widget
        assert not widget.white_bottom
        # Play 1...e5 by clicking: Black's view flips files and ranks.
        def offset(square):
            file_index = 7 - chess.square_file(square)
            rank_row = chess.square_rank(square)
            return (2 + file_index * widget.cell_w + 1, rank_row * widget.cell_h + 1)
        await pilot.click(ChessBoard, offset=offset(chess.E7))
        await pilot.pause()
        assert widget.selected == chess.E7
        await pilot.click(ChessBoard, offset=offset(chess.E5))
        assert await _settle(pilot, screen, lambda: len(screen.drill.sans) >= 3)
        assert screen.drill.sans[:3] == ["e4", "e5", "Nf3"]


@pytest.mark.asyncio
async def test_learn_mode_shows_the_move(book, evaluator, tmp_path):
    spec, line = _spec(book, mode=LEARN)
    app = make_app(book, evaluator, tmp_path, spec)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert "Play e4" in screen.feed[-1].plain
        assert screen.board_widget.hints == {chess.E2, chess.E4}
        await pilot.press("e", "4", "enter")
        assert await _settle(pilot, screen, lambda: len(screen.drill.sans) >= 2)
        assert not app.progress.data["lines"]  # lessons are not graded


@pytest.mark.asyncio
async def test_free_board_any_side(book, evaluator, tmp_path):
    app = make_app(book, evaluator, tmp_path, DrillSpec([], chess.WHITE, FREE, "Free"))
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("e", "4", "enter")
        assert await _settle(pilot, screen, lambda: not screen.board_widget.locked)
        await pilot.press("e", "5", "enter")
        assert await _settle(pilot, screen, lambda: len(screen.drill.sans) == 2 and not screen.board_widget.locked)
        await pilot.press("u")
        await pilot.pause()
        assert screen.drill.sans == ["e4"]


@pytest.mark.asyncio
async def test_compact_board_on_small_terminal(book, evaluator, tmp_path):
    spec, _ = _spec(book)
    app = make_app(book, evaluator, tmp_path, spec)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.screen.board_widget.compact


@pytest.mark.asyncio
async def test_browse_search_and_start(book, evaluator, tmp_path):
    app = make_app(book, evaluator, tmp_path)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*"najdorf")
        await pilot.pause()
        assert app.screen.results and "Najdorf" in app.screen.results[0].name
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # Learn this line
        await pilot.pause()
        assert isinstance(app.screen, DrillScreen)
        assert app.screen.drill.mode == LEARN


@pytest.mark.asyncio
async def test_invalid_typed_move_is_reported(book, evaluator, tmp_path):
    spec, _ = _spec(book)
    app = make_app(book, evaluator, tmp_path, spec)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("e", "5", "enter")
        await pilot.pause()
        assert "Could not play" in screen.feed[-1].plain
        assert screen.drill.sans == []
