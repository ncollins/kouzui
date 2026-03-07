import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    default_listening_port: int = 50881
    stream_chunk_size: int = 1024 * 8
    block_size: int = 1024 * 8
    internal_queue_size: int = 100
    max_outstanding_requests_per_peer: int = 30
    keepalive_seconds: int = 115
    num_unchoked_peers: int = 4
    delete_stale_requests_seconds: int = 10 * 60
    # Set to 0 in TOML to disable rate limiting
    max_outgoing_bytes_per_second: int | None = 6 * 1024**2


DEFAULT_CONFIG = Config()


def load_config(path: Path) -> Config:
    """Load a Config from a TOML file, using defaults for any missing fields."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    d = DEFAULT_CONFIG
    raw_rate_limit = data.get("max_outgoing_bytes_per_second", d.max_outgoing_bytes_per_second)
    max_outgoing: int | None = None if raw_rate_limit == 0 else raw_rate_limit
    return Config(
        default_listening_port=data.get("default_listening_port", d.default_listening_port),
        stream_chunk_size=data.get("stream_chunk_size", d.stream_chunk_size),
        block_size=data.get("block_size", d.block_size),
        internal_queue_size=data.get("internal_queue_size", d.internal_queue_size),
        max_outstanding_requests_per_peer=data.get(
            "max_outstanding_requests_per_peer", d.max_outstanding_requests_per_peer
        ),
        keepalive_seconds=data.get("keepalive_seconds", d.keepalive_seconds),
        num_unchoked_peers=data.get("num_unchoked_peers", d.num_unchoked_peers),
        delete_stale_requests_seconds=data.get(
            "delete_stale_requests_seconds", d.delete_stale_requests_seconds
        ),
        max_outgoing_bytes_per_second=max_outgoing,
    )
