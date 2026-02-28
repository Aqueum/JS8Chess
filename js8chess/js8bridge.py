"""JS8Call TCP API bridge.

Connects to JS8Call on its TCP API port (default 2442).
Sends and receives JSON messages, calling a user-supplied callback for
every inbound RX.DIRECTED message.

JS8Call API message format (newline-delimited JSON):
  Inbound:  {"type": "RX.DIRECTED", "value": {"FROM": "...", "TO": "...", "TEXT": "..."}}
  Outbound: {"type": "TX.SEND_MESSAGE", "value": {"TO": "...", "TEXT": "..."}}

Note: the exact field names may vary between JS8Call versions.  The bridge
logs raw messages at DEBUG level so operators can calibrate if needed.
"""

import json
import logging
import socket
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Maximum time (seconds) to wait between reconnect attempts
RECONNECT_DELAY = 10
SOCKET_TIMEOUT = 5.0


class JS8Bridge:
    """Maintains a persistent TCP connection to JS8Call and dispatches messages.

    Args:
        host: JS8Call API host (typically 127.0.0.1).
        port: JS8Call API port (typically 2442).
        on_message: Callback invoked with (from_call: str, to_call: str, text: str)
                    for every directed message received.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_message: Callable[[str, str, str], None],
    ) -> None:
        self._host = host
        self._port = port
        self._on_message = on_message

        self._sock: Optional[socket.socket] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._buffer = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the bridge (connect + begin background receive loop)."""
        self._running = True
        self._recv_thread = threading.Thread(
            target=self._recv_loop, name="js8-recv", daemon=True
        )
        self._recv_thread.start()
        log.info("JS8Bridge started, targeting %s:%d", self._host, self._port)

    def stop(self) -> None:
        """Shut down the bridge.

        The recv thread is a daemon thread; closing the socket is enough to
        unblock any pending recv().  We do not join() to avoid delaying quit
        while a connection attempt is in progress.
        """
        self._running = False
        self._close_socket()
        log.info("JS8Bridge stopped")

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send(self, to_callsign: str, text: str) -> bool:
        """Send a directed message via JS8Call.

        Returns True if the bytes were written to the socket successfully.
        The actual radio transmission may be delayed by JS8Call's own PTT
        and scheduling logic.
        """
        msg = {
            "type": "TX.SEND_MESSAGE",
            "value": {
                "TO": to_callsign.upper(),
                "TEXT": text,
            },
        }
        return self._send_json(msg)

    def _send_json(self, obj: dict) -> bool:
        raw = json.dumps(obj) + "\n"
        with self._lock:
            if self._sock is None:
                log.warning("Cannot send: not connected to JS8Call")
                return False
            try:
                self._sock.sendall(raw.encode("utf-8"))
                log.debug("JS8Call TX: %s", raw.rstrip())
                return True
            except OSError as exc:
                log.error("Send failed: %s", exc)
                self._close_socket()
                return False

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    def _recv_loop(self) -> None:
        """Background thread: maintain connection and dispatch inbound messages."""
        while self._running:
            if self._sock is None:
                self._connect()
                if self._sock is None:
                    # Connection failed; wait before retrying
                    time.sleep(RECONNECT_DELAY)
                    continue

            try:
                data = self._sock.recv(4096)
                if not data:
                    log.warning("JS8Call closed the connection; reconnecting...")
                    self._close_socket()
                    continue
                self._buffer += data.decode("utf-8", errors="replace")
                self._process_buffer()
            except socket.timeout:
                continue  # normal, just loop again
            except OSError as exc:
                if self._running:
                    log.error("Receive error: %s; reconnecting...", exc)
                self._close_socket()

    def _connect(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SOCKET_TIMEOUT)
            sock.connect((self._host, self._port))
            with self._lock:
                self._sock = sock
                self._buffer = ""
            log.info("Connected to JS8Call at %s:%d", self._host, self._port)
        except OSError as exc:
            log.warning("JS8Call connection failed (%s:%d): %s", self._host, self._port, exc)

    def _close_socket(self) -> None:
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def _process_buffer(self) -> None:
        """Extract and dispatch complete JSON messages from the buffer."""
        # Messages are newline-terminated; handle both \n and \r\n
        while True:
            nl = self._buffer.find("\n")
            if nl == -1:
                break
            line = self._buffer[:nl].strip()
            self._buffer = self._buffer[nl + 1:]
            if not line:
                continue
            log.debug("JS8Call RX raw: %s", line)
            self._dispatch(line)

    def _dispatch(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.debug("Non-JSON data from JS8Call: %s (%s)", raw, exc)
            return

        msg_type = msg.get("type", "")
        value = msg.get("value", {})

        if msg_type in ("RX.DIRECTED", "RX.DIRECTED.ME"):
            # Extract fields; JS8Call versions differ slightly in naming
            from_call = value.get("FROM", value.get("from", "")).upper().strip()
            to_call = value.get("TO", value.get("to", "")).upper().strip()
            text = value.get("TEXT", value.get("text", value.get("VALUE", ""))).strip()
            log.info("JS8Call directed: FROM=%s TO=%s TEXT=%r", from_call, to_call, text)
            try:
                self._on_message(from_call, to_call, text)
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("on_message callback raised: %s", exc)
        else:
            log.debug("Ignored JS8Call event type: %s", msg_type)

    @property
    def connected(self) -> bool:
        return self._sock is not None
