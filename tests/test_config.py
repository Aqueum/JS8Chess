"""Tests for configuration loading."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from js8chess.config import load_config, DEFAULT_CONFIG


@pytest.fixture
def tmp_config_dir(tmp_path):
    with patch("js8chess.config.CONFIG_DIR", tmp_path), \
         patch("js8chess.config.CONFIG_FILE", tmp_path / "config.json"):
        yield tmp_path


class TestLoadConfig:
    def test_creates_default_when_missing(self, tmp_config_dir):
        cfg = load_config()
        config_file = tmp_config_dir / "config.json"
        assert config_file.exists()
        assert cfg.local_callsign == DEFAULT_CONFIG["local_callsign"].upper()

    def test_loads_custom_values(self, tmp_config_dir):
        config_file = tmp_config_dir / "config.json"
        custom = {
            "local_callsign": "G0ABC",
            "remote_callsign": "G0DEF",
            "js8_host": "192.168.1.1",
            "js8_port": 9999,
            "ack_wait_seconds": 30,
            "move_response_wait_seconds": 90,
            "max_retries": 5,
        }
        config_file.write_text(json.dumps(custom))
        cfg = load_config()
        assert cfg.local_callsign == "G0ABC"
        assert cfg.remote_callsign == "G0DEF"
        assert cfg.js8_host == "192.168.1.1"
        assert cfg.js8_port == 9999
        assert cfg.ack_wait_seconds == 30
        assert cfg.move_response_wait_seconds == 90
        assert cfg.max_retries == 5

    def test_callsigns_uppercased(self, tmp_config_dir):
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({**DEFAULT_CONFIG, "local_callsign": "g0abc"}))
        cfg = load_config()
        assert cfg.local_callsign == "G0ABC"

    def test_missing_key_filled_with_default(self, tmp_config_dir):
        config_file = tmp_config_dir / "config.json"
        partial = {"local_callsign": "G0ABC", "remote_callsign": "G0DEF"}
        config_file.write_text(json.dumps(partial))
        cfg = load_config()
        assert cfg.js8_port == DEFAULT_CONFIG["js8_port"]
        assert cfg.max_retries == DEFAULT_CONFIG["max_retries"]
