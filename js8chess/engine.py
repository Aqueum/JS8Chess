"""JS8Chess engine: UCI loop + game logic + ACK/retry state machine.

Architecture
------------
Main thread   : UCI stdin/stdout loop
js8-recv      : JS8Bridge background receiver (in js8bridge.py)
go-handler    : Spawned per UCI "go" command to await remote moves

Shared state is protected by self._state_lock.
Incoming radio moves are delivered via self._radio_queue (thread-safe).
"""

import chess
import logging
import queue
import sys
import threading
import time
from enum import Enum, auto
from typing import List, Optional

from .config import Config
from .game import GameSession
from .js8bridge import JS8Bridge
from . import protocol as proto

log = logging.getLogger(__name__)

# Sentinel placed in radio_queue to unblock a waiting go-handler on stop/quit
_STOP_SENTINEL = object()


class EngineState(Enum):
    NO_GAME = auto()
    PROPOSAL_SENT = auto()       # We sent NEW, awaiting acceptance
    AWAITING_PROPOSAL = auto()   # Waiting for remote to send NEW (or we will send one)
    GAME_ACTIVE = auto()
    GAME_OVER = auto()


class JS8ChessEngine:
    """UCI engine that bridges chess moves over JS8Call radio.

    Lifecycle
    ---------
    1.  Instantiate with a loaded Config.
    2.  Call run() — this blocks, reading UCI from stdin until "quit".
    """

    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._state = EngineState.NO_GAME
        self._state_lock = threading.Lock()

        # Active game session (set once a game is accepted)
        self._game: Optional[GameSession] = None

        # Queued messages arriving from radio (proto.OTAMessage objects)
        self._radio_queue: queue.Queue = queue.Queue()

        # Last UCI position's move list (to detect new local moves)
        self._last_position_moves: List[str] = []

        # Event to interrupt a waiting go-handler (stop / quit)
        self._go_stop_event = threading.Event()

        # Bridge to JS8Call
        self._bridge = JS8Bridge(
            host=config.js8_host,
            port=config.js8_port,
            on_message=self._on_radio_message,
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block and run the UCI loop until quit."""
        self._bridge.start()
        log.info(
            "JS8Chess engine ready — local: %s, remote: %s",
            self._cfg.local_callsign,
            self._cfg.remote_callsign,
        )
        try:
            self._uci_loop()
        finally:
            self._bridge.stop()

    # ------------------------------------------------------------------
    # UCI input loop
    # ------------------------------------------------------------------

    def _uci_loop(self) -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            cmd = line.strip()
            if not cmd:
                continue
            log.debug("UCI in: %s", cmd)
            self._handle_uci(cmd)

    def _handle_uci(self, cmd: str) -> None:
        parts = cmd.split()
        verb = parts[0] if parts else ""

        if verb == "uci":
            self._uci_out("id name JS8Chess")
            self._uci_out("id author JS8Chess Project")
            self._uci_out("uciok")

        elif verb == "isready":
            self._uci_out("readyok")

        elif verb == "ucinewgame":
            with self._state_lock:
                self._game = None
                self._state = EngineState.NO_GAME
                self._last_position_moves = []
            log.info("ucinewgame received — session cleared")

        elif verb == "position":
            self._handle_position(parts[1:])

        elif verb == "go":
            self._handle_go()

        elif verb == "stop":
            self._go_stop_event.set()

        elif verb == "quit":
            self._go_stop_event.set()
            sys.exit(0)

        else:
            log.debug("Unknown UCI command: %r", cmd)

    # ------------------------------------------------------------------
    # position command
    # ------------------------------------------------------------------

    def _handle_position(self, tokens: List[str]) -> None:
        """Parse 'position startpos moves ...' and record the move list."""
        if not tokens:
            return

        moves: List[str] = []
        try:
            if "moves" in tokens:
                idx = tokens.index("moves")
                moves = [m.lower() for m in tokens[idx + 1:]]
        except ValueError:
            pass

        self._last_position_moves = moves
        log.debug("Position moves: %s", moves)

    # ------------------------------------------------------------------
    # go command
    # ------------------------------------------------------------------

    def _handle_go(self) -> None:
        """Spawn a thread to handle the go command asynchronously."""
        self._go_stop_event.clear()
        t = threading.Thread(target=self._go_handler, name="go-handler", daemon=True)
        t.start()

    def _go_handler(self) -> None:
        """Determine and transmit any new local move, then await remote's move."""
        with self._state_lock:
            game = self._game
            state = self._state

        # --- No game yet — check if we are awaiting a radio game proposal ---
        if state == EngineState.NO_GAME or state == EngineState.AWAITING_PROPOSAL:
            # Wait for a game to become active via radio negotiation
            self._uci_out("info string Waiting for JS8Chess game to be established via radio")
            remote_move = self._await_radio_move(timeout=None)  # block until stop
            if remote_move is None:
                self._uci_out("bestmove 0000")
                return

        with self._state_lock:
            game = self._game
            state = self._state

        if state != EngineState.GAME_ACTIVE or game is None:
            self._uci_out("info string No active JS8Chess game")
            self._uci_out("bestmove 0000")
            return

        # --- Detect new local moves ---
        gui_moves = list(self._last_position_moves)
        board_moves = game.move_list_uci()

        new_moves = gui_moves[len(board_moves):]

        if new_moves:
            # Apply and transmit each new local move
            for uci_move in new_moves:
                if not self._apply_and_transmit_local_move(game, uci_move):
                    self._uci_out("bestmove 0000")
                    return
        else:
            # No new local move — it may be remote's turn (e.g. local is Black
            # and game just started, or after a resync)
            if game.is_local_turn():
                log.warning("go received but no new local move and it is local's turn")

        # --- Await remote's reply ---
        with self._state_lock:
            if self._game is None or self._state != EngineState.GAME_ACTIVE:
                self._uci_out("bestmove 0000")
                return

        self._uci_out("info string Waiting for remote move via radio")
        remote_move = self._await_radio_move(
            timeout=None  # wait indefinitely; retransmit logic is in retry loop
        )

        if remote_move is None:
            # Stopped or error
            self._uci_out("bestmove 0000")
            return

        self._uci_out(f"bestmove {remote_move}")

    # ------------------------------------------------------------------
    # Local move: apply + transmit with retries
    # ------------------------------------------------------------------

    def _apply_and_transmit_local_move(self, game: GameSession, uci_move: str) -> bool:
        """Validate, apply, and transmit a local move.  Returns True on success."""
        if not game.validate_move(uci_move):
            log.error("Invalid local move from GUI: %s", uci_move)
            self._uci_out(f"info string ERROR: invalid local move {uci_move}")
            return False

        move_num = game.expected_move_num
        if not game.apply_move(uci_move):
            return False

        ota_text = proto.fmt_move(
            self._cfg.local_callsign,
            self._cfg.remote_callsign,
            move_num,
            uci_move,
        )
        self._uci_out(f"info string TX: {ota_text}")
        log.info("Transmitting local move: %s", ota_text)

        # Transmit with retry (move messages wait for remote's next move, not ACK)
        for attempt in range(1, self._cfg.max_retries + 1):
            self._bridge.send(self._cfg.remote_callsign, ota_text)
            log.info("Move transmitted (attempt %d/%d)", attempt, self._cfg.max_retries)
            # No wait here — the go_handler's await_radio_move handles the timeout
            break  # transmit once; retransmit is triggered by timeout below

        return True

    # ------------------------------------------------------------------
    # Await remote move from radio queue
    # ------------------------------------------------------------------

    def _await_radio_move(self, timeout: Optional[float]) -> Optional[str]:
        """Block until a validated remote move arrives, or stop is signalled.

        Returns the UCI move string on success, None on stop/error.
        If timeout is None, uses move_response_wait_seconds per retry cycle.
        """
        cfg = self._cfg
        wait = cfg.move_response_wait_seconds if timeout is None else timeout
        retries_left = cfg.max_retries

        while not self._go_stop_event.is_set():
            try:
                item = self._radio_queue.get(timeout=min(wait, 5.0))
            except queue.Empty:
                # Check stop event first
                if self._go_stop_event.is_set():
                    return None
                wait -= 5.0
                if timeout is None and wait <= 0:
                    # Retry: retransmit the last local move
                    if retries_left > 0:
                        retries_left -= 1
                        self._retransmit_last_local_move()
                        wait = cfg.move_response_wait_seconds
                    else:
                        self._uci_out("info string ERROR: no response after max retries")
                        return None
                continue

            if item is _STOP_SENTINEL:
                return None

            # item is an OTAMessage
            msg: proto.OTAMessage = item
            return self._process_radio_item(msg)

        return None

    def _process_radio_item(self, msg: proto.OTAMessage) -> Optional[str]:
        """Process a received OTA message.  Returns UCI move string or None."""
        game = self._game

        if msg.msg_type == proto.MsgType.ACK:
            log.debug("ACK received")
            return None  # ACK alone doesn't advance state; keep waiting

        if msg.msg_type == proto.MsgType.MOVE:
            if game is None:
                self._send_error(proto.ERR_NO_SESSION)
                return None

            # Validate move number
            if msg.move_num != game.expected_move_num:
                log.warning(
                    "Move number mismatch: expected %d, got %d",
                    game.expected_move_num, msg.move_num,
                )
                self._send_error(proto.ERR_BAD_MOVE_NUM)
                return None

            # Validate move legality
            if not game.validate_move(msg.move):
                log.warning("Illegal remote move: %s", msg.move)
                self._send_error(proto.ERR_ILLEGAL_MOVE)
                return None

            # Apply to board
            game.apply_move(msg.move)
            self._uci_out(f"info string RX move: {msg.move_num}{msg.move.upper()}")
            log.info("Remote move applied: %s (ply %d)", msg.move, game.expected_move_num - 1)
            return msg.move

        if msg.msg_type == proto.MsgType.ERROR:
            self._uci_out(f"info string Remote sent error: {msg.error_code}")
            log.error("Remote error received: %s", msg.error_code)
            return None

        if msg.msg_type == proto.MsgType.RESYNC_REQUEST:
            self._handle_resync_request(msg)
            return None  # keep waiting after resync

        if msg.msg_type == proto.MsgType.RESYNC_OK:
            log.info("Resync OK received for game %s MN=%d", msg.timestamp, msg.resync_move_num)
            return None

        log.debug("Unhandled message type in go-handler: %s", msg.msg_type)
        return None

    # ------------------------------------------------------------------
    # Retransmit
    # ------------------------------------------------------------------

    def _retransmit_last_local_move(self) -> None:
        """Retransmit the last local move (called on timeout)."""
        game = self._game
        if game is None:
            return
        # The last local move is the one just before expected_move_num
        move_list = game.move_list_uci()
        if not move_list:
            return
        last_local_ply = game.expected_move_num - 1  # 1-based
        if last_local_ply < 1 or last_local_ply > len(move_list):
            return
        last_move = move_list[last_local_ply - 1]
        ota_text = proto.fmt_move(
            self._cfg.local_callsign,
            self._cfg.remote_callsign,
            last_local_ply,
            last_move,
        )
        self._uci_out(f"info string RETRY TX: {ota_text}")
        log.info("Retransmitting: %s", ota_text)
        self._bridge.send(self._cfg.remote_callsign, ota_text)

    # ------------------------------------------------------------------
    # Error sending
    # ------------------------------------------------------------------

    def _send_error(self, code: str) -> None:
        text = proto.fmt_error(
            self._cfg.local_callsign,
            self._cfg.remote_callsign,
            code,
        )
        desc = proto.ERR_DESCRIPTIONS.get(code, "")
        self._uci_out(f"info string TX error {code}: {desc}")
        log.info("Sending error: %s (%s)", code, desc)
        self._bridge.send(self._cfg.remote_callsign, text)

    # ------------------------------------------------------------------
    # Radio message callback (called from js8-recv thread)
    # ------------------------------------------------------------------

    def _on_radio_message(self, from_call: str, to_call: str, text: str) -> None:
        """Called by JS8Bridge for every received directed message."""
        msg = proto.parse_message(
            text,
            local_call=self._cfg.local_callsign,
            remote_call=self._cfg.remote_callsign,
            from_call=from_call,
        )
        if msg is None:
            return  # Not a JS8Chess message for us

        log.info("Radio RX: type=%s from=%s", msg.msg_type.name, from_call)

        # Route based on current engine state
        with self._state_lock:
            state = self._state

        if msg.msg_type == proto.MsgType.NEW_PROPOSAL:
            self._handle_new_proposal(msg)
        elif msg.msg_type == proto.MsgType.ACCEPTANCE:
            self._handle_acceptance(msg)
        elif state == EngineState.GAME_ACTIVE:
            # Deliver to go-handler via queue
            self._radio_queue.put(msg)
        elif msg.msg_type == proto.MsgType.RESYNC_REQUEST:
            self._handle_resync_request(msg)
        else:
            log.debug("Ignoring message in state %s: %s", state, msg.msg_type)

    # ------------------------------------------------------------------
    # Game negotiation handlers
    # ------------------------------------------------------------------

    def _handle_new_proposal(self, msg: proto.OTAMessage) -> None:
        """Remote is proposing a new game."""
        with self._state_lock:
            if self._state == EngineState.GAME_ACTIVE:
                log.warning("NEW proposal ignored: game already active")
                return
            self._state = EngineState.AWAITING_PROPOSAL

        log.info("NEW proposal received: remote wants to play as %s", msg.color)

        if not self._cfg.auto_accept:
            self._uci_out(
                f"info string Game proposal from {msg.from_call} ignored "
                f"(auto_accept is false in config)"
            )
            log.info("Proposal ignored: auto_accept disabled")
            with self._state_lock:
                self._state = EngineState.NO_GAME
            return

        # The acceptance timestamp becomes the canonical game ID
        timestamp = proto.now_timestamp()

        # Proposed color is remote's color; our color is the opposite
        remote_color_str = msg.color  # "W" or "B" — remote's color
        local_color = chess.BLACK if remote_color_str == "W" else chess.WHITE
        local_color_str = "B" if remote_color_str == "W" else "W"

        acceptance = proto.fmt_acceptance(
            self._cfg.local_callsign,
            self._cfg.remote_callsign,
            timestamp,
            local_color_str,   # our color in the acceptance message
        )
        self._uci_out(f"info string TX acceptance: {acceptance}")
        log.info("Sending acceptance: %s", acceptance)
        self._bridge.send(self._cfg.remote_callsign, acceptance)

        # Start the game session
        with self._state_lock:
            self._game = GameSession(
                game_id=timestamp,
                local_callsign=self._cfg.local_callsign,
                remote_callsign=self._cfg.remote_callsign,
                local_color=local_color,
            )
            self._state = EngineState.GAME_ACTIVE
            self._last_position_moves = []

        self._uci_out(
            f"info string Game started — ID: {timestamp}, "
            f"local: {local_color_str}, remote: {remote_color_str}"
        )
        log.info(
            "Game active — ID=%s local=%s remote=%s",
            timestamp, local_color_str, remote_color_str,
        )

        # If remote is White they move first — unblock any waiting go-handler
        # by doing nothing (the go-handler will await the first radio move)

    def _handle_acceptance(self, msg: proto.OTAMessage) -> None:
        """Remote accepted our NEW proposal."""
        with self._state_lock:
            if self._state != EngineState.PROPOSAL_SENT:
                log.debug("Acceptance ignored: not in PROPOSAL_SENT state")
                return

            # msg.color is the acceptor's (remote's) color
            remote_color_str = msg.color
            local_color = chess.WHITE if remote_color_str == "B" else chess.BLACK
            local_color_str = "W" if remote_color_str == "B" else "B"

            self._game = GameSession(
                game_id=msg.timestamp,
                local_callsign=self._cfg.local_callsign,
                remote_callsign=self._cfg.remote_callsign,
                local_color=local_color,
            )
            self._state = EngineState.GAME_ACTIVE
            self._last_position_moves = []

        self._uci_out(
            f"info string Game accepted — ID: {msg.timestamp}, "
            f"local: {local_color_str}, remote: {remote_color_str}"
        )
        log.info(
            "Game accepted — ID=%s local=%s remote=%s",
            msg.timestamp, local_color_str, remote_color_str,
        )

    # ------------------------------------------------------------------
    # Resync handler
    # ------------------------------------------------------------------

    def _handle_resync_request(self, msg: proto.OTAMessage) -> None:
        """Remote requested resynchronisation to a specific move number."""
        game = self._game
        if game is None:
            log.warning("Resync requested but no active game")
            self._send_error(proto.ERR_NO_SESSION)
            return

        if msg.timestamp != game.game_id:
            log.warning(
                "Resync timestamp mismatch: got %s expected %s",
                msg.timestamp, game.game_id,
            )
            self._send_error(proto.ERR_DESYNC)
            return

        target_ply = msg.resync_move_num - 1  # move_num is next expected; restore to previous
        ok = game.restore_to_ply(target_ply)
        if not ok:
            self._send_error(proto.ERR_DESYNC)
            return

        response = proto.fmt_resync_ok(
            self._cfg.local_callsign,
            self._cfg.remote_callsign,
            game.game_id,
            msg.resync_move_num,
        )
        self._uci_out(f"info string TX resync OK: {response}")
        log.info("Sending resync OK: %s", response)
        self._bridge.send(self._cfg.remote_callsign, response)

    # ------------------------------------------------------------------
    # Proposal helpers (for sending our own NEW)
    # ------------------------------------------------------------------

    def send_new_proposal(self, color: str = "W") -> None:
        """Transmit a NEW game proposal (called externally or on startup)."""
        with self._state_lock:
            if self._state == EngineState.GAME_ACTIVE:
                log.warning("Cannot send NEW: game already active")
                return
            self._state = EngineState.PROPOSAL_SENT

        text = proto.fmt_new_proposal(
            self._cfg.local_callsign,
            self._cfg.remote_callsign,
            color,
        )
        self._uci_out(f"info string TX new proposal: {text}")
        log.info("Sending NEW proposal: %s", text)
        self._bridge.send(self._cfg.remote_callsign, text)

    # ------------------------------------------------------------------
    # UCI output helper
    # ------------------------------------------------------------------

    def _uci_out(self, line: str) -> None:
        print(line, flush=True)
        log.debug("UCI out: %s", line)
