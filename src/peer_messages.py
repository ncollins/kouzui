from dataclasses import dataclass
from enum import IntEnum
from typing import TypeAlias

import bitarray

from shared_types import Block, PeerId


class MessageTypeByte(IntEnum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8


@dataclass(frozen=True, kw_only=True, slots=True)
class RawPeerMessage:
    msg_type: int
    payload: bytes


def parse_have(s: bytes) -> int:
    return int.from_bytes(s[:4], byteorder="big")


def parse_bitfield(s: bytes) -> bitarray.bitarray:
    # NOTE the input will be an integer number of bytes, so it may
    # have extra bits
    b = bitarray.bitarray()
    b.frombytes(s)
    return b


def parse_request_or_cancel(s: bytes) -> Block:
    # This should be 12 bytes in most cases, so I'm hardcoding it for now.
    return Block(
        piece_index=int.from_bytes(s[:4], byteorder="big"),
        block_start=int.from_bytes(s[4:8], byteorder="big"),
        block_length=int.from_bytes(s[8:], byteorder="big"),
    )


def parse_piece(s):
    index = int.from_bytes(s[:4], byteorder="big")
    begin = int.from_bytes(s[4:8], byteorder="big")
    data = s[8:]
    return (index, begin, data)


@dataclass(frozen=True, kw_only=True, slots=True)
class Request:
    blocks: set[Block]


@dataclass(frozen=True, kw_only=True, slots=True)
class Have:
    piece_index: int


@dataclass(frozen=True, kw_only=True, slots=True)
class Piece:
    peer_id: PeerId
    block: Block
    data: bytes


@dataclass(frozen=True, kw_only=True, slots=True)
class Choke:
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class Unchoke:
    pass


PeerMessage: TypeAlias = Request | Have | Piece | Choke | Unchoke
