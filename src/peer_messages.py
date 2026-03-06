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
class Request:
    blocks: set[Block]

    def to_bytes(self) -> list[bytes]:
        msgs: list[bytes] = []
        for block in self.blocks:
            raw = bytes([MessageTypeByte.REQUEST])
            raw += block.piece_index.to_bytes(4, byteorder="big")
            raw += block.block_start.to_bytes(4, byteorder="big")
            raw += block.block_length.to_bytes(4, byteorder="big")
            msgs.append(raw)
        return msgs


@dataclass(frozen=True, kw_only=True, slots=True)
class Have:
    piece_index: int

    def to_bytes(self) -> list[bytes]:
        raw = bytes([MessageTypeByte.HAVE])
        raw += self.piece_index.to_bytes(4, byteorder="big")
        return [raw]


@dataclass(frozen=True, kw_only=True, slots=True)
class Piece:
    peer_id: PeerId
    block: Block
    data: bytes

    def to_bytes(self) -> list[bytes]:
        raw = bytes([MessageTypeByte.PIECE])
        raw += self.block.piece_index.to_bytes(4, byteorder="big")
        raw += self.block.block_start.to_bytes(4, byteorder="big")
        raw += self.data
        return [raw]


@dataclass(frozen=True, kw_only=True, slots=True)
class Choke:
    def to_bytes(self) -> list[bytes]:
        return [bytes([MessageTypeByte.CHOKE])]


@dataclass(frozen=True, kw_only=True, slots=True)
class Unchoke:
    def to_bytes(self) -> list[bytes]:
        return [bytes([MessageTypeByte.UNCHOKE])]


@dataclass(frozen=True, kw_only=True, slots=True)
class Bitfield:
    pieces: bitarray.bitarray

    def to_bytes(self) -> list[bytes]:
        raw = bytes([MessageTypeByte.BITFIELD])
        raw += self.pieces.tobytes()
        return [raw]


@dataclass(frozen=True, kw_only=True, slots=True)
class Interested:
    def to_bytes(self) -> list[bytes]:
        return [bytes([MessageTypeByte.INTERESTED])]


@dataclass(frozen=True, kw_only=True, slots=True)
class NotInterested:
    def to_bytes(self) -> list[bytes]:
        return [bytes([MessageTypeByte.NOT_INTERESTED])]


@dataclass(frozen=True, kw_only=True, slots=True)
class Cancel:
    block: Block

    def to_bytes(self) -> list[bytes]:
        raw = bytes([MessageTypeByte.CANCEL])
        raw += self.block.piece_index.to_bytes(4, byteorder="big")
        raw += self.block.block_start.to_bytes(4, byteorder="big")
        raw += self.block.block_length.to_bytes(4, byteorder="big")
        return [raw]


PeerMessage: TypeAlias = (
    Request | Have | Piece | Choke | Unchoke | Bitfield | Interested | NotInterested | Cancel
)


def parse_message(peer_id: PeerId, data: bytes) -> PeerMessage:
    """Parse raw message bytes (with type byte) into a typed PeerMessage."""
    msg_type = data[0]
    payload = data[1:]
    match msg_type:
        case MessageTypeByte.CHOKE:
            return Choke()
        case MessageTypeByte.UNCHOKE:
            return Unchoke()
        case MessageTypeByte.INTERESTED:
            return Interested()
        case MessageTypeByte.NOT_INTERESTED:
            return NotInterested()
        case MessageTypeByte.HAVE:
            piece_index = int.from_bytes(payload[:4], byteorder="big")
            return Have(piece_index=piece_index)
        case MessageTypeByte.BITFIELD:
            b = bitarray.bitarray()
            b.frombytes(payload)
            return Bitfield(pieces=b)
        case MessageTypeByte.REQUEST:
            block = Block(
                piece_index=int.from_bytes(payload[:4], byteorder="big"),
                block_start=int.from_bytes(payload[4:8], byteorder="big"),
                block_length=int.from_bytes(payload[8:], byteorder="big"),
            )
            return Request(blocks={block})
        case MessageTypeByte.PIECE:
            piece_index = int.from_bytes(payload[:4], byteorder="big")
            block_start = int.from_bytes(payload[4:8], byteorder="big")
            block_data = payload[8:]
            block = Block(
                piece_index=piece_index,
                block_start=block_start,
                block_length=len(block_data),
            )
            return Piece(peer_id=peer_id, block=block, data=block_data)
        case MessageTypeByte.CANCEL:
            block = Block(
                piece_index=int.from_bytes(payload[:4], byteorder="big"),
                block_start=int.from_bytes(payload[4:8], byteorder="big"),
                block_length=int.from_bytes(payload[8:], byteorder="big"),
            )
            return Cancel(block=block)
        case _:
            raise ValueError(f"Unknown message type: {msg_type}, payload: {payload!r}")
