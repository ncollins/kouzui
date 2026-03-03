from dataclasses import dataclass
from typing import TypeAlias

PeerId: TypeAlias = bytes


@dataclass(frozen=True, kw_only=True, slots=True)
class Block:
    piece_index: int
    block_start: int
    block_length: int


@dataclass(frozen=True, kw_only=True, slots=True)
class PeerAddress:
    ip: bytes
    port: int

    def __post_init__(self):
        if not isinstance(self.ip, bytes) or not isinstance(self.port, int):
            raise ValueError(
                f"Incorrect types when initializing PeerAddress: ip={self.ip!r} should be bytes and port={self.port} should be int"
            )
