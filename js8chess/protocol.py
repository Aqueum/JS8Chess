"""Over-the-air protocol parsing and formatting for JS8Chess.

All OTA text is UPPERCASE. Internally moves are lowercase UCI notation.

Message formats:
  NEW proposal:   REMOTECALL LOCALCALL JS8CHESS NEW W/B
  Acceptance:     LOCALCALL REMOTECALL JS8CHESS YYYYMMDDHHMM W/B
  Move:           REMOTECALL LOCALCALL JS8CHESS <MOVENUM><MOVE>
  Error:          REMOTECALL LOCALCALL JS8CHESS ERR0X >
  Resync request: REMOTECALL LOCALCALL JS8CHESS RS YYYYMMDDHHMM MN=N
  Resync OK:      LOCALCALL REMOTECALL JS8CHESS OK RS YYYYMMDDHHMM MN=N
"""

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional

log = logging.getLogger(__name__)


class MsgType(Enum):
    NEW_PROPOSAL = auto()
    ACCEPTANCE = auto()
    MOVE = auto()
    ACK = auto()
    ERROR = auto()
    RESYNC_REQUEST = auto()
    RESYNC_OK = auto()
    UNKNOWN = auto()


# Error codes
ERR_ILLEGAL_MOVE = "ERR01"
ERR_BAD_MOVE_NUM = "ERR02"
ERR_NO_SESSION = "ERR03"
ERR_PARSE = "ERR04"
ERR_DESYNC = "ERR05"

ERR_DESCRIPTIONS = {
    "ERR01": "Illegal move",
    "ERR02": "Unexpected move number",
    "ERR03": "Not in active session",
    "ERR04": "Protocol parse error",
    "ERR05": "State desync detected",
}


@dataclass
class OTAMessage:
    from_call: str
    to_call: str
    msg_type: MsgType
    color: Optional[str] = None          # "W" or "B"
    timestamp: Optional[str] = None      # YYYYMMDDHHMM
    move_num: Optional[int] = None
    move: Optional[str] = None           # lowercase UCI
    error_code: Optional[str] = None
    resync_move_num: Optional[int] = None


def parse_message(
    raw_text: str,
    local_call: str,
    remote_call: str,
    from_call: str = "",
) -> Optional[OTAMessage]:
    """Parse a raw text string into an OTAMessage.

    Returns None if the message is not a JS8Chess message directed to us.
    from_call is the FROM field from the JS8Call API (if available).
    """
    text = raw_text.strip().upper()

    # Normalise callsigns
    local = local_call.upper()
    remote = remote_call.upper()

    # We accept messages that begin with either:
    #   REMOTE LOCAL JS8CHESS ...
    # The FROM callsign check is an extra safety layer when available.
    prefix = f"{remote} {local} JS8CHESS"
    if not text.startswith(prefix):
        # Also try without the leading callsigns (some API modes strip FROM)
        bare_prefix = f"{local} JS8CHESS"
        if text.startswith(bare_prefix):
            text = f"{remote} {text}"  # reconstruct
        else:
            return None

    # Optional: validate FROM field matches expected remote callsign
    if from_call and from_call.upper() != remote:
        log.debug("Ignoring message from unexpected callsign: %s", from_call)
        return None

    payload = text[len(prefix):].strip()

    # --- ACK ---
    if payload in (">", ""):
        return OTAMessage(remote, local, MsgType.ACK)

    # --- NEW proposal ---
    m = re.fullmatch(r"NEW ([WB])", payload)
    if m:
        return OTAMessage(remote, local, MsgType.NEW_PROPOSAL, color=m.group(1))

    # --- Acceptance: YYYYMMDDHHMM W/B ---
    m = re.fullmatch(r"(\d{12}) ([WB])", payload)
    if m:
        return OTAMessage(
            remote, local, MsgType.ACCEPTANCE,
            timestamp=m.group(1), color=m.group(2),
        )

    # --- Error: ERR0X > ---
    m = re.fullmatch(r"(ERR0[1-5])\s*>?", payload)
    if m:
        return OTAMessage(remote, local, MsgType.ERROR, error_code=m.group(1))

    # --- Resync request: RS YYYYMMDDHHMM MN=N ---
    m = re.fullmatch(r"RS (\d{12}) MN=(\d+)", payload)
    if m:
        return OTAMessage(
            remote, local, MsgType.RESYNC_REQUEST,
            timestamp=m.group(1), resync_move_num=int(m.group(2)),
        )

    # --- Resync OK: OK RS YYYYMMDDHHMM MN=N ---
    m = re.fullmatch(r"OK RS (\d{12}) MN=(\d+)", payload)
    if m:
        return OTAMessage(
            remote, local, MsgType.RESYNC_OK,
            timestamp=m.group(1), resync_move_num=int(m.group(2)),
        )

    # --- Move: <MOVENUM><MOVE> ---
    # Move is coordinate notation: E2E4, E7E8Q, etc.
    m = re.fullmatch(r"(\d+)([A-H][1-8][A-H][1-8][QRBN]?)", payload)
    if m:
        return OTAMessage(
            remote, local, MsgType.MOVE,
            move_num=int(m.group(1)),
            move=m.group(2).lower(),  # store lowercase
        )

    log.warning("Unrecognised JS8Chess payload: %r", payload)
    return OTAMessage(remote, local, MsgType.UNKNOWN)


# ---------------------------------------------------------------------------
# Formatters — produce UPPERCASE OTA strings
# ---------------------------------------------------------------------------

def fmt_new_proposal(local: str, remote: str, color: str) -> str:
    """REMOTE LOCAL JS8CHESS NEW W/B"""
    return f"{remote} {local} JS8CHESS NEW {color.upper()}"


def fmt_acceptance(local: str, remote: str, timestamp: str, color: str) -> str:
    """LOCAL REMOTE JS8CHESS YYYYMMDDHHMM W/B"""
    return f"{local} {remote} JS8CHESS {timestamp} {color.upper()}"


def fmt_move(local: str, remote: str, move_num: int, move_uci: str) -> str:
    """REMOTE LOCAL JS8CHESS <MOVENUM><MOVE>"""
    return f"{remote} {local} JS8CHESS {move_num}{move_uci.upper()}"


def fmt_error(local: str, remote: str, code: str) -> str:
    """REMOTE LOCAL JS8CHESS ERR0X >"""
    return f"{remote} {local} JS8CHESS {code} >"


def fmt_resync_request(local: str, remote: str, timestamp: str, move_num: int) -> str:
    """REMOTE LOCAL JS8CHESS RS YYYYMMDDHHMM MN=N"""
    return f"{remote} {local} JS8CHESS RS {timestamp} MN={move_num}"


def fmt_resync_ok(local: str, remote: str, timestamp: str, move_num: int) -> str:
    """LOCAL REMOTE JS8CHESS OK RS YYYYMMDDHHMM MN=N"""
    return f"{local} {remote} JS8CHESS OK RS {timestamp} MN={move_num}"


def now_timestamp() -> str:
    """Return current local time as YYYYMMDDHHMM."""
    return datetime.now().strftime("%Y%m%d%H%M")
