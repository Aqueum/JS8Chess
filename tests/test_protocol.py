"""Tests for the OTA protocol parser and formatters."""

import pytest
from js8chess.protocol import (
    MsgType, parse_message,
    fmt_new_proposal, fmt_acceptance, fmt_move, fmt_error,
    fmt_resync_request, fmt_resync_ok,
)

LOCAL = "CALLSIGN"
REMOTE = "SWL"


# ---------------------------------------------------------------------------
# parse_message
# ---------------------------------------------------------------------------

def _parse(text, from_call=""):
    return parse_message(text, LOCAL, REMOTE, from_call=from_call)


class TestParseNewProposal:
    def test_white(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS NEW W")
        assert msg is not None
        assert msg.msg_type == MsgType.NEW_PROPOSAL
        assert msg.color == "W"
        assert msg.from_call == REMOTE
        assert msg.to_call == LOCAL

    def test_black(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS NEW B")
        assert msg.msg_type == MsgType.NEW_PROPOSAL
        assert msg.color == "B"

    def test_lowercase_input_normalised(self):
        msg = _parse(f"{REMOTE.lower()} {LOCAL.lower()} js8chess new w")
        assert msg.msg_type == MsgType.NEW_PROPOSAL

    def test_wrong_callsign_returns_none(self):
        msg = _parse(f"G0ABC {LOCAL} JS8CHESS NEW W")
        assert msg is None

    def test_from_call_mismatch_returns_none(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS NEW W", from_call="G0ABC")
        assert msg is None

    def test_from_call_match_accepted(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS NEW W", from_call=REMOTE)
        assert msg is not None


class TestParseAcceptance:
    def test_valid(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS 202506011430 W")
        assert msg.msg_type == MsgType.ACCEPTANCE
        assert msg.timestamp == "202506011430"
        assert msg.color == "W"

    def test_black_acceptance(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS 202506011430 B")
        assert msg.msg_type == MsgType.ACCEPTANCE
        assert msg.color == "B"

    def test_wrong_timestamp_length_not_acceptance(self):
        # 11 digits instead of 12 — should not match acceptance
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS 20250601143 W")
        assert msg is None or msg.msg_type != MsgType.ACCEPTANCE


class TestParseMove:
    def test_move_1_e2e4(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS 1E2E4")
        assert msg.msg_type == MsgType.MOVE
        assert msg.move_num == 1
        assert msg.move == "e2e4"

    def test_move_2_e7e5(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS 2E7E5")
        assert msg.move_num == 2
        assert msg.move == "e7e5"

    def test_promotion(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS 15E7E8Q")
        assert msg.move_num == 15
        assert msg.move == "e7e8q"

    def test_move_stored_lowercase(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS 3D2D4")
        assert msg.move == "d2d4"

    def test_multi_digit_move_num(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS 42A1A8")
        assert msg.move_num == 42
        assert msg.move == "a1a8"


class TestParseAck:
    def test_ack(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS >")
        assert msg.msg_type == MsgType.ACK


class TestParseError:
    @pytest.mark.parametrize("code", ["ERR01", "ERR02", "ERR03", "ERR04", "ERR05"])
    def test_errors(self, code):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS {code} >")
        assert msg.msg_type == MsgType.ERROR
        assert msg.error_code == code

    def test_error_without_ack_marker(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS ERR01")
        assert msg.msg_type == MsgType.ERROR


class TestParseResync:
    def test_resync_request(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS RS 202506011430 MN=17")
        assert msg.msg_type == MsgType.RESYNC_REQUEST
        assert msg.timestamp == "202506011430"
        assert msg.resync_move_num == 17

    def test_resync_ok(self):
        msg = _parse(f"{REMOTE} {LOCAL} JS8CHESS OK RS 202506011430 MN=17")
        assert msg.msg_type == MsgType.RESYNC_OK
        assert msg.timestamp == "202506011430"
        assert msg.resync_move_num == 17


class TestParseUnrelated:
    def test_not_js8chess(self):
        msg = _parse(f"{REMOTE} {LOCAL} HELLO THERE")
        assert msg is None

    def test_empty_string(self):
        msg = _parse("")
        assert msg is None

    def test_unrelated_traffic(self):
        msg = _parse("CQ CQ DE SWL")
        assert msg is None


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

class TestFormatters:
    def test_new_proposal_white(self):
        txt = fmt_new_proposal(LOCAL, REMOTE, "W")
        assert txt == f"{REMOTE} {LOCAL} JS8CHESS NEW W"

    def test_new_proposal_lowercase_normalised(self):
        txt = fmt_new_proposal(LOCAL, REMOTE, "w")
        assert "W" in txt

    def test_acceptance(self):
        txt = fmt_acceptance(LOCAL, REMOTE, "202506011430", "B")
        assert txt == f"{LOCAL} {REMOTE} JS8CHESS 202506011430 B"

    def test_move_uppercase(self):
        txt = fmt_move(LOCAL, REMOTE, 1, "e2e4")
        assert txt == f"{REMOTE} {LOCAL} JS8CHESS 1E2E4"

    def test_move_promotion(self):
        txt = fmt_move(LOCAL, REMOTE, 15, "e7e8q")
        assert "E7E8Q" in txt

    def test_error(self):
        txt = fmt_error(LOCAL, REMOTE, "ERR01")
        assert txt == f"{REMOTE} {LOCAL} JS8CHESS ERR01 >"

    def test_resync_request(self):
        txt = fmt_resync_request(LOCAL, REMOTE, "202506011430", 17)
        assert txt == f"{REMOTE} {LOCAL} JS8CHESS RS 202506011430 MN=17"

    def test_resync_ok(self):
        txt = fmt_resync_ok(LOCAL, REMOTE, "202506011430", 17)
        assert txt == f"{LOCAL} {REMOTE} JS8CHESS OK RS 202506011430 MN=17"

    def test_round_trip_move(self):
        """Format then parse a move and verify we recover the same data."""
        original_move = "d2d4"
        original_num = 3
        txt = fmt_move(LOCAL, REMOTE, original_num, original_move)
        msg = _parse(txt)
        assert msg.msg_type == MsgType.MOVE
        assert msg.move == original_move
        assert msg.move_num == original_num
