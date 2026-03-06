from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True, slots=True)
class Config:
    default_listening_port: int = 50881
    stream_chunk_size: int = 1024 * 8
    block_size: int = 1024 * 8
    internal_queue_size: int = 100
    max_outstanding_requests_per_peer: int = 30
    keepalive_seconds: int = 115
    num_unchoked_peers: int = 4
    delete_stale_requests_seconds: int = 10 * 60
    max_outgoing_bytes_per_second: int | None = 6 * 1024**2


DEFAULT_CONFIG = Config()


def load_config(path: Path) -> Config:
    """Load a Config from a TOML file. Missing keys use defaults."""
    with path.open("rb") as f:
        data = tomllib.load(f)
    return Config(**data)
