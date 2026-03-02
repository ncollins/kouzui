#!/usr/bin/env python3
"""
Integration test data utilities.

Subcommands:

  create-incomplete-files  <test_dir> <torrent_name> <tracker_url>
      Generates a random source file, a .torrent pointing at the given
      tracker, and one partial file per client (pieces distributed
      round-robin so the swarm collectively holds the full file but no
      single client can complete without downloading from peers).

  verify-complete-files  <test_dir> <torrent_name>
      Confirms that each client's completed download exists and is
      byte-for-byte identical to the original source file.

Examples:
    # compose
    uv run integration_tests/test_data.py create-incomplete-files \\
        integration_tests/test_data test_file.bin http://tracker:8000/announce
    uv run integration_tests/test_data.py verify-complete-files \\
        integration_tests/test_data test_file.bin

    # kube
    uv run integration_tests/test_data.py create-incomplete-files \\
        integration_tests/test_data_kube test_file.bin http://localhost:8000/announce
    uv run integration_tests/test_data.py verify-complete-files \\
        integration_tests/test_data_kube test_file.bin
"""

import hashlib
import os
import pathlib

import typer

app = typer.Typer()

FILE_SIZE = 2 * 1024 * 1024  # 2 MB
PIECE_LENGTH = 256 * 1024  # 256 KB  →  8 pieces total
NUM_CLIENTS = 3  # keep in sync with compose.yml / pod.yml


# ---------------------------------------------------------------------------
# Minimal bencoder (no external dependencies)
# Keys are written in insertion order; the engine's bencode.encode_dict also
# preserves insertion order, so the info_hash round-trips correctly.
# ---------------------------------------------------------------------------
def _bencode(v):
    if isinstance(v, bytes):
        return b"%d:%s" % (len(v), v)
    if isinstance(v, str):
        enc = v.encode()
        return b"%d:%s" % (len(enc), enc)
    if isinstance(v, int):
        return b"i%de" % v
    if isinstance(v, list):
        return b"l" + b"".join(_bencode(x) for x in v) + b"e"
    if isinstance(v, dict):
        return b"d" + b"".join(_bencode(k) + _bencode(v_) for k, v_ in v.items()) + b"e"
    raise TypeError(f"Unsupported type for bencoding: {type(v)}")


def _sha256(path: pathlib.Path) -> bytes:
    return hashlib.sha256(path.read_bytes()).digest()


@app.command()
def create_incomplete_files(
    test_dir: pathlib.Path = typer.Argument(..., help="Directory to write test data into"),
    torrent_name: str = typer.Argument(
        ..., help="File name used inside the torrent (e.g. test_file.bin)"
    ),
    tracker_url: str = typer.Argument(..., help="Announce URL written into the .torrent file"),
):
    """Generate source file, .torrent, and per-client partial files."""
    test_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create the complete source file from random bytes
    print(f"Creating {torrent_name} ({FILE_SIZE // 1024} KB) ...")
    data = os.urandom(FILE_SIZE)
    (test_dir / torrent_name).write_bytes(data)

    # 2. Compute per-piece SHA1 hashes
    piece_count = (FILE_SIZE + PIECE_LENGTH - 1) // PIECE_LENGTH
    pieces_hash = b""
    for i in range(piece_count):
        start = i * PIECE_LENGTH
        pieces_hash += hashlib.sha1(data[start : start + PIECE_LENGTH]).digest()

    # 3. Write the .torrent file
    # The engine computes info_hash by re-encoding torrent_data[b"info"] with
    # bencode.encode_value, which preserves dict insertion order.  We must use
    # the same key order here so the hash is stable across the parse/encode
    # round-trip.
    torrent_info = {
        b"length": FILE_SIZE,
        b"name": torrent_name.encode(),
        b"piece length": PIECE_LENGTH,
        b"pieces": pieces_hash,
    }
    torrent_dict = {
        b"announce": tracker_url.encode(),
        b"info": torrent_info,
    }
    torrent_path = test_dir / "test.torrent"
    print(f"Writing {torrent_path} ...")
    torrent_path.write_bytes(_bencode(torrent_dict))

    # 4. Create one partial file per client
    # Pieces are assigned round-robin so every piece exists in the swarm
    # and no single client can complete the download without its peers.
    print(f"Creating {NUM_CLIENTS} partial files ...")
    partial_data = [bytearray(FILE_SIZE) for _ in range(NUM_CLIENTS)]
    for i in range(piece_count):
        start = i * PIECE_LENGTH
        end = min(start + PIECE_LENGTH, FILE_SIZE)
        partial_data[i % NUM_CLIENTS][start:end] = data[start:end]

    for i, pd in enumerate(partial_data):
        out = test_dir / f"{torrent_name}.{i}.part"
        out.write_bytes(pd)
        owned = sum(1 for p in range(piece_count) if p % NUM_CLIENTS == i)
        print(f"  {out.name}  ({owned}/{piece_count} pieces pre-loaded)")

    print(f"\nDone. Test data written to {test_dir}")


@app.command()
def verify_complete_files(
    test_dir: pathlib.Path = typer.Argument(
        ..., help="Directory that was passed to create-incomplete-files"
    ),
    torrent_name: str = typer.Argument(
        ..., help="File name used inside the torrent (e.g. test_file.bin)"
    ),
):
    """Verify each client downloaded a complete, correct copy of the file."""
    reference = test_dir / torrent_name
    if not reference.exists():
        typer.echo(f"ERROR: reference file not found: {reference}", err=True)
        raise typer.Exit(code=1)

    reference_checksum = _sha256(reference)

    failures = []
    for n in range(NUM_CLIENTS):
        path = test_dir / "clients" / f"client-{n}" / torrent_name
        if not path.exists():
            failures.append(f"  MISSING  {path}")
        elif _sha256(path) != reference_checksum:
            failures.append(f"  CHECKSUM MISMATCH  {path}")
        else:
            typer.echo(f"  OK  {path}")

    if failures:
        typer.echo("\nVerification failed:", err=True)
        for msg in failures:
            typer.echo(msg, err=True)
        raise typer.Exit(code=1)

    typer.echo(f"\nAll {NUM_CLIENTS} client files verified successfully.")


if __name__ == "__main__":
    app()
