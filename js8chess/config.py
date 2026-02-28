"""Configuration loading for JS8Chess."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".js8chess"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: dict = {
    "local_callsign": "CALLSIGN",
    "remote_callsign": "SWL",
    "js8_host": "127.0.0.1",
    "js8_port": 2442,
    "ack_wait_seconds": 60,
    "move_response_wait_seconds": 120,
    "max_retries": 3,
    "auto_accept": True,
}


@dataclass
class Config:
    local_callsign: str
    remote_callsign: str
    js8_host: str
    js8_port: int
    ack_wait_seconds: int
    move_response_wait_seconds: int
    max_retries: int
    auto_accept: bool


def load_config() -> Config:
    """Load config from ~/.js8chess/config.json, creating defaults if absent."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        log.info("Created default config at %s", CONFIG_FILE)
        data = DEFAULT_CONFIG.copy()
    else:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        # Fill in any missing keys with defaults
        for k, v in DEFAULT_CONFIG.items():
            data.setdefault(k, v)

    return Config(
        local_callsign=data["local_callsign"].upper(),
        remote_callsign=data["remote_callsign"].upper(),
        js8_host=data["js8_host"],
        js8_port=int(data["js8_port"]),
        ack_wait_seconds=int(data["ack_wait_seconds"]),
        move_response_wait_seconds=int(data["move_response_wait_seconds"]),
        max_retries=int(data["max_retries"]),
        auto_accept=bool(data["auto_accept"]),
    )
