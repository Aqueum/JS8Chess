"""Tests for GameSession: move application, PGN, resync."""

import chess
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from js8chess.game import GameSession


@pytest.fixture
def tmp_pgn_dir(tmp_path):
    """Patch PGN_DIR to use a temp directory."""
    with patch("js8chess.game.PGN_DIR", tmp_path):
        yield tmp_path


@pytest.fixture
def session(tmp_pgn_dir):
    return GameSession(
        game_id="202506011430",
        local_callsign="MM7MMU",
        remote_callsign="MM7XYZ",
        local_color=chess.WHITE,
    )


class TestGameSessionInit:
    def test_initial_move_num(self, session):
        assert session.expected_move_num == 1

    def test_local_color(self, session):
        assert session.local_color == chess.WHITE
        assert session.remote_color == chess.BLACK

    def test_pgn_headers(self, session):
        headers = session._pgn_game.headers
        assert headers["White"] == "MM7MMU"
        assert headers["Black"] == "MM7XYZ"
        assert headers["Event"] == "JS8Chess Radio Game"
        assert headers["Date"] == "2025.06.01"
        assert headers["Result"] == "*"

    def test_pgn_path_format(self, session, tmp_pgn_dir):
        expected = tmp_pgn_dir / "MM7XYZ-202506011430.pgn"
        assert session.pgn_path == expected


class TestMoveApplication:
    def test_valid_move_advances_board(self, session):
        assert session.apply_move("e2e4")
        assert len(session.board.move_stack) == 1

    def test_move_num_increments(self, session):
        assert session.expected_move_num == 1
        session.apply_move("e2e4")
        assert session.expected_move_num == 2
        session.apply_move("e7e5")
        assert session.expected_move_num == 3

    def test_invalid_move_rejected(self, session):
        assert not session.apply_move("e2e5")  # illegal pawn jump

    def test_illegal_move_does_not_advance(self, session):
        session.apply_move("e2e5")
        assert session.expected_move_num == 1

    def test_move_from_wrong_side_rejected(self, session):
        # It's White's turn; attempting a Black move
        assert not session.apply_move("e7e5")

    def test_sequence_of_moves(self, session):
        moves = ["e2e4", "e7e5", "d2d4", "d7d5"]
        for m in moves:
            assert session.apply_move(m), f"Failed to apply: {m}"
        assert session.expected_move_num == 5


class TestValidateMove:
    def test_legal_move_returns_true(self, session):
        assert session.validate_move("e2e4")

    def test_illegal_move_returns_false(self, session):
        assert not session.validate_move("e2e5")

    def test_nonsense_returns_false(self, session):
        assert not session.validate_move("zzzz")

    def test_out_of_range_returns_false(self, session):
        assert not session.validate_move("a9a1")


class TestTurnTracking:
    def test_local_turn_at_start(self, session):
        # local is White, White moves first
        assert session.is_local_turn()
        assert not session.is_remote_turn()

    def test_remote_turn_after_white_move(self, session):
        session.apply_move("e2e4")
        assert session.is_remote_turn()
        assert not session.is_local_turn()

    def test_black_local_awaits_remote_first(self, tmp_pgn_dir):
        black_session = GameSession(
            game_id="202506011430",
            local_callsign="MM7MMU",
            remote_callsign="MM7XYZ",
            local_color=chess.BLACK,
        )
        # Black's turn is second; initially it is White's (remote) turn
        assert not black_session.is_local_turn()
        assert black_session.is_remote_turn()


class TestMovelist:
    def test_empty_at_start(self, session):
        assert session.move_list_uci() == []

    def test_moves_returned_in_order(self, session):
        session.apply_move("e2e4")
        session.apply_move("e7e5")
        assert session.move_list_uci() == ["e2e4", "e7e5"]


class TestPGNPersistence:
    def test_pgn_file_created_after_move(self, session):
        session.apply_move("e2e4")
        assert session.pgn_path.exists()

    def test_pgn_contains_move(self, session):
        session.apply_move("e2e4")
        content = session.pgn_path.read_text()
        assert "e4" in content  # PGN uses algebraic notation

    def test_pgn_headers_in_file(self, session):
        session.apply_move("e2e4")
        content = session.pgn_path.read_text()
        assert "JS8Chess Radio Game" in content
        assert "MM7MMU" in content


class TestResync:
    def test_restore_to_ply_2(self, session):
        # Play 4 moves, then restore to ply 2
        session.apply_move("e2e4")
        session.apply_move("e7e5")
        session.apply_move("d2d4")
        session.apply_move("d7d5")
        assert session.expected_move_num == 5

        ok = session.restore_to_ply(2)
        assert ok
        assert session.expected_move_num == 3
        assert len(session.board.move_stack) == 2

    def test_restore_to_ply_0(self, session):
        session.apply_move("e2e4")
        session.apply_move("e7e5")
        ok = session.restore_to_ply(0)
        assert ok
        assert session.expected_move_num == 1

    def test_restore_missing_pgn_fails(self, tmp_pgn_dir):
        s = GameSession("202506011430", "MM7MMU", "MM7XYZ", chess.WHITE)
        # PGN file doesn't exist yet
        ok = s.restore_to_ply(3)
        assert not ok

    def test_restore_preserves_correct_turn(self, session):
        session.apply_move("e2e4")  # ply 1 — now Black's turn
        session.apply_move("e7e5")  # ply 2 — now White's turn
        session.restore_to_ply(1)
        # After 1 ply (White moved), it should be Black's turn
        assert session.board.turn == chess.BLACK


class TestSetResult:
    def test_set_result(self, session):
        session.apply_move("e2e4")
        session.set_result("1-0")
        content = session.pgn_path.read_text()
        assert "1-0" in content
