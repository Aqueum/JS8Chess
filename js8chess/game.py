"""Game state management: board, move history, PGN persistence, and resync."""

import logging
from pathlib import Path
from typing import List, Optional

import chess
import chess.pgn

log = logging.getLogger(__name__)

PGN_DIR = Path.home() / ".js8chess"


class GameSession:
    """Maintains authoritative game state for one JS8Chess radio game.

    Move numbering follows the spec: sequential integers starting at 1,
    incrementing after every half-move (ply).  White's first move is 1,
    Black's reply is 2, White's second move is 3, etc.
    """

    def __init__(
        self,
        game_id: str,           # YYYYMMDDHHMM canonical timestamp
        local_callsign: str,
        remote_callsign: str,
        local_color: chess.Color,   # chess.WHITE or chess.BLACK
    ) -> None:
        self.game_id = game_id
        self.local_callsign = local_callsign.upper()
        self.remote_callsign = remote_callsign.upper()
        self.local_color = local_color
        self.remote_color: chess.Color = not local_color

        self.board = chess.Board()
        self._pgn_game = chess.pgn.Game()
        self._pgn_node: chess.pgn.GameNode = self._pgn_game

        white_call = local_callsign if local_color == chess.WHITE else remote_callsign
        black_call = remote_callsign if local_color == chess.WHITE else local_callsign
        date_str = f"{game_id[:4]}.{game_id[4:6]}.{game_id[6:8]}"

        self._pgn_game.headers.update({
            "Event": "JS8Chess Radio Game",
            "Date": date_str,
            "White": white_call.upper(),
            "Black": black_call.upper(),
            "Result": "*",
        })

        PGN_DIR.mkdir(parents=True, exist_ok=True)
        self.pgn_path = PGN_DIR / f"{self.remote_callsign}-{self.game_id}.pgn"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def expected_move_num(self) -> int:
        """Next expected OTA move number (1-based sequential ply count)."""
        return len(self.board.move_stack) + 1

    def is_local_turn(self) -> bool:
        return self.board.turn == self.local_color

    def is_remote_turn(self) -> bool:
        return self.board.turn == self.remote_color

    def move_list_uci(self) -> List[str]:
        """Return all played moves as UCI strings (lowercase)."""
        board = chess.Board()
        moves = []
        for move in self.board.move_stack:
            moves.append(move.uci())
            board.push(move)
        return moves

    # ------------------------------------------------------------------
    # Move application
    # ------------------------------------------------------------------

    def validate_move(self, uci_move: str) -> bool:
        """Return True if the move is legal in the current position."""
        try:
            move = chess.Move.from_uci(uci_move)
            return move in self.board.legal_moves
        except (ValueError, chess.InvalidMoveError):
            return False

    def apply_move(self, uci_move: str) -> bool:
        """Apply a move to the board and PGN.  Returns True on success."""
        try:
            move = chess.Move.from_uci(uci_move)
            if move not in self.board.legal_moves:
                log.warning("Illegal move rejected: %s", uci_move)
                return False
            self.board.push(move)
            self._pgn_node = self._pgn_node.add_variation(move)
            self._save_pgn()
            log.info("Move applied: %s (ply %d)", uci_move, len(self.board.move_stack))
            return True
        except (ValueError, chess.InvalidMoveError) as exc:
            log.warning("Move parse error %r: %s", uci_move, exc)
            return False

    # ------------------------------------------------------------------
    # PGN persistence
    # ------------------------------------------------------------------

    def _save_pgn(self) -> None:
        try:
            with open(self.pgn_path, "w") as f:
                exporter = chess.pgn.FileExporter(f)
                self._pgn_game.accept(exporter)
        except OSError as exc:
            log.error("PGN save failed: %s", exc)

    # ------------------------------------------------------------------
    # Resync
    # ------------------------------------------------------------------

    def restore_to_ply(self, target_ply: int) -> bool:
        """Reload PGN and restore board to the state after <target_ply> plies.

        target_ply corresponds to OTA move number - 1 (since move numbers are
        1-based and represent the NEXT expected move).
        Returns True on success.
        """
        if not self.pgn_path.exists():
            log.error("PGN file not found for resync: %s", self.pgn_path)
            return False
        try:
            with open(self.pgn_path) as f:
                game = chess.pgn.read_game(f)
            if game is None:
                log.error("Could not parse PGN for resync")
                return False

            board = chess.Board()
            node: chess.pgn.GameNode = game
            applied = 0
            for move in game.mainline_moves():
                if applied >= target_ply:
                    break
                board.push(move)
                node = node.next()  # type: ignore[assignment]
                applied += 1

            self.board = board
            self._pgn_game = game
            self._pgn_node = node
            log.info("Resync complete: restored to ply %d", applied)
            return True
        except Exception as exc:  # pylint: disable=broad-except
            log.error("Resync restore failed: %s", exc)
            return False

    def set_result(self, result: str) -> None:
        """Record game result (*  1-0  0-1  1/2-1/2)."""
        self._pgn_game.headers["Result"] = result
        self._save_pgn()
