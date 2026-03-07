# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kouzui is a single-file BitTorrent client written in Python using the [Trio](https://trio.readthedocs.io) async library. It downloads single-file torrents from peers, verifies piece integrity, and supports concurrent uploads/downloads. It intentionally omits multi-file torrents, magnet links, UDP trackers, and rarest-piece-first selection.

## Commands

All commands use `uv run` for environment isolation.

```bash
make check           # Run all checks: format-check + lint + typecheck + unit-test
make unit-test       # Run pytest (includes doctests in src/)
make typecheck       # Run mypy + ty
make lint            # Run ruff check
make format          # Auto-format with ruff
make integration-test  # Requires Podman; runs multi-client swarm test
make install         # uv tool install --python python3.11 .
```

To run a single test file: `uv run pytest unit_tests/test_bencode.py`

## Architecture

### Concurrency Model

The engine (`src/engine.py`) is the core. It spawns ~10 concurrent Trio tasks inside a single nursery, all communicating via bounded `trio.MemoryChannel` queues:

- `peer_clients_loop` / `peer_server_loop` — manage outgoing/incoming peer TCP connections
- `tracker_loop` — periodically queries the HTTP tracker for peers
- `peer_messages_loop` — routes messages from all peers to the engine
- `file_write_confirmation_loop` / `file_reading_loop` — interface with `FileManager`
- `choking_loop` — runs tit-for-tat every 10s, optimistic unchoke every 30s
- `delete_stale_requests_loop` — cleans up timed-out block requests

Each peer connection (`src/peer_connection.py`) runs its own nursery with tasks for reading, writing, and keepalives.

### Data Flow

```
Tracker → peer addresses → Engine
Engine → connect to peers → PeerConnection tasks
PeerConnections → raw messages → Engine (via peer_messages channel)
Engine → parsed requests → FileManager (via channels)
FileManager → write confirmations / read data → Engine
```

### Key Modules

| File | Role |
|------|------|
| `engine.py` | Orchestrates all tasks; piece selection, choking, request tracking |
| `peer_connection.py` | BitTorrent handshake, message framing, per-peer send queue |
| `file_manager.py` | Piece assembly, SHA1 validation, disk I/O, `.part` → final rename |
| `torrent.py` | Parses `.torrent` files, computes `info_hash` |
| `bencode.py` | BitTorrent serialization (also has doctests) |
| `peer_state.py` | Per-peer state: bitfield, choke status, rolling download stats |
| `requests.py` | Tracks outstanding block requests per peer |
| `token_bucket.py` | Rate limiting (default 6 MB/s outgoing, configurable in `config.py`) |
| `shared_types.py` | Core dataclasses: `Block`, `PeerAddress`, `PeerId` |
| `internal_messages.py` | Channel message types between Engine and FileManager |

### Piece/Block Model

- **Piece**: unit validated by SHA1 hash from `.torrent` file
- **Block**: 8 KB sub-unit of a piece (defined in `config.py` as `BLOCK_SIZE`)
- Downloads to a `.part` file; renamed on completion
- Supports resuming: existing file is hash-checked at startup

## Type Checking

The project uses both `mypy` and `ty` in strict mode. All functions must have complete type annotations (`disallow_incomplete_defs = true`). Dataclasses use `frozen=True` and `slots=True` where possible.

## Testing

- Unit tests: `unit_tests/` (currently only `test_bencode.py`)
- Doctests: embedded in `src/` modules (run automatically via pytest)
- Integration tests: `integration_tests/` — requires Podman, builds container images, runs a tracker + multiple Kouzui clients in a pod

## Configuration

Key constants in `src/config.py`:
- `DEFAULT_LISTENING_PORT = 50881`
- `BLOCK_SIZE = 8192` (8 KB)
- `MAX_OUTSTANDING_REQUESTS_PER_PEER = 30`
- `NUM_UNCHOKED_PEERS = 4`
- `MAX_OUTGOING_BYTES_PER_SECOND = 6_000_000` (can be `None` to disable)
