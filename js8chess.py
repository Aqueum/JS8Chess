#!/usr/bin/env python3
"""JS8Chess — UCI chess engine bridge over JS8Call radio.

Usage:
    python js8chess.py [--propose W|B] [--loglevel DEBUG|INFO|WARNING]

Options:
    --propose W|B    On startup, immediately transmit a NEW game proposal
                     offering to play as White (W) or Black (B).
    --loglevel       Log verbosity (default: INFO).

The engine reads UCI commands from stdin and writes responses to stdout.
All logging goes to ~/.js8chess/js8chess.log (and stderr at WARNING+).
"""

import argparse
import logging
import sys
from pathlib import Path

from js8chess.config import load_config
from js8chess.engine import JS8ChessEngine


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    log_dir = Path.home() / ".js8chess"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "js8chess.log"

    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stderr),
        ],
    )
    # Quieten noisy libraries
    logging.getLogger("chess").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="JS8Chess UCI engine")
    parser.add_argument(
        "--propose",
        metavar="W|B",
        choices=["W", "B", "w", "b"],
        help="Send a NEW game proposal as White (W) or Black (B) on startup",
    )
    parser.add_argument(
        "--loglevel",
        default="INFO",
        metavar="LEVEL",
        help="Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)",
    )
    args = parser.parse_args()

    setup_logging(args.loglevel)
    log = logging.getLogger("js8chess")
    log.info("JS8Chess starting")

    config = load_config()
    log.info(
        "Config loaded: local=%s remote=%s js8call=%s:%d",
        config.local_callsign, config.remote_callsign,
        config.js8_host, config.js8_port,
    )

    engine = JS8ChessEngine(config)

    if args.propose:
        engine.send_new_proposal(args.propose.upper())

    engine.run()


if __name__ == "__main__":
    main()
