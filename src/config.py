import tomllib
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class Config(BaseModel):
    default_listening_port: int = Field(ge=1, le=65535)
    stream_chunk_size: int = Field(gt=0)
    block_size: int = Field(gt=0)
    internal_queue_size: int = Field(gt=0)
    max_outstanding_requests_per_peer: int = Field(gt=0)
    keepalive_seconds: int = Field(gt=0)
    num_unchoked_peers: int = Field(ge=0)
    delete_stale_requests_seconds: int = Field(gt=0)
    max_outgoing_bytes_per_second: Optional[int] = Field(gt=0)


_DEFAULT_CONFIG = Config(
    default_listening_port=50881,
    stream_chunk_size=1024 * 8,
    block_size=1024 * 8,
    internal_queue_size=100,
    max_outstanding_requests_per_peer=30,
    keepalive_seconds=115,
    num_unchoked_peers=4,
    delete_stale_requests_seconds=10 * 60,
    max_outgoing_bytes_per_second=6 * 1024**2,
)


def load_config(path: Optional[Path] = None) -> Config:
    if path is None:
        return _DEFAULT_CONFIG
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Config(**data)
