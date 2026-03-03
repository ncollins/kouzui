from dataclasses import dataclass
from typing import TypeAlias

PeerId: TypeAlias = bytes


@dataclass(frozen=True, kw_only=True, slots=True)
class Block:
    piece_index: int
    block_start: int
    block_length: int
