# JS8Chess

WARNING UNTESTED

A lightweight UCI chess engine bridge that lets two radio operators play chess over [JS8Call](https://github.com/JS8Call-improved).

JS8Chess allows you to use a chess GUI (Arena, CuteChess, Banksia or any UCI-compatible GUIs) to play with a remote opponent, transmitting your moves using JS8 over HF radio and waiting for theirs.

```
Chess GUI (UCI)
     ↕  stdin/stdout
  JS8Chess
     ↕  TCP
  JS8Call
     ↕
   Radio
```

---

## Requirements

- Python 3.8+
- [JS8Call](https://github.com/JS8Call-improved) running with its TCP API enabled (default port 2442)
- A UCI-compatible chess GUI (Arena, CuteChess, Banksia, etc.)

---

## Installation

```bash
git clone https://github.com/Aqueum/JS8Chess.git
cd JS8Chess
pip install chess
```

---

## Setup

### 1. Configure

On first run JS8Chess creates `~/.js8chess/config.json` with defaults:

```json
{
  "local_callsign": "MM7MMU",
  "remote_callsign": "MM7XYZ",
  "js8_host": "127.0.0.1",
  "js8_port": 2442,
  "ack_wait_seconds": 60,
  "move_response_wait_seconds": 120,
  "max_retries": 3
}
```

Edit it to match your callsign, your opponent's callsign, and your JS8Call API address.

### 2. Enable JS8Call TCP API

In JS8Call: **File → Settings → Reporting** — enable the TCP API on port 2442.

### 3. Register with your chess GUI

Point your GUI to JS8Chess as a UCI engine:

```
python3 /path/to/js8chess.py
```

---

## Starting a game

### Responding to a proposal

If your opponent transmits a NEW proposal, JS8Chess accepts it automatically and the game begins. Just have your GUI ready.

### Sending a proposal

Pass `--propose` when starting the engine:

```bash
python3 js8chess.py --propose W   # offer to play as White
python3 js8chess.py --propose B   # offer to play as Black
```

---

## Playing

Once a game is established:

1. Make your move in the GUI as normal.
2. JS8Chess validates and transmits it over radio.
3. JS8Chess waits for your opponent's reply.
4. When their move arrives, the GUI advances automatically.

---

## Over-the-air protocol

All transmissions are uppercase. Move numbers are sequential half-moves (ply) starting at 1.

| Message | Format |
|---|---|
| New game proposal | `REMOTECALL LOCALCALL JS8CHESS NEW W` |
| Acceptance | `LOCALCALL REMOTECALL JS8CHESS YYYYMMDDHHMM W` |
| Move | `REMOTECALL LOCALCALL JS8CHESS 1E2E4` |
| Error | `REMOTECALL LOCALCALL JS8CHESS ERR01 >` |
| Resync request | `REMOTECALL LOCALCALL JS8CHESS RS YYYYMMDDHHMM MN=17` |
| Resync OK | `LOCALCALL REMOTECALL JS8CHESS OK RS YYYYMMDDHHMM MN=17` |

Error codes: `ERR01` illegal move · `ERR02` wrong move number · `ERR03` no session · `ERR04` parse error · `ERR05` desync

The acceptance timestamp (`YYYYMMDDHHMM`) becomes the canonical game identifier and the PGN filename.

---

## Resync

If QRM or a missed transmission causes a desync, either side can resync using their chess GUI's move list as reference. JS8Chess will reload from the local PGN file and both sides continue from the agreed move number.

PGN files are saved to `~/.js8chess/<REMOTECALL>-<YYYYMMDDHHMM>.pgn`.

---

## Logging

```bash
python3 js8chess.py --loglevel DEBUG
```

Logs are written to `~/.js8chess/js8chess.log` and stderr.

---

## Running tests

```bash
python3 -m pytest tests/
```

---

## Design notes

- No AI, no GUI — pure UCI bridge
- No per-move identifiers beyond move number
- Minimal characters per transmission
- Deterministic recovery via timestamp + move number
- Assumes amateur radio transparency; recovery via explicit RS rather than prevention
