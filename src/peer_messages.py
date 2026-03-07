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
class Choke:
    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.CHOKE])


@dataclass(frozen=True, kw_only=True, slots=True)
class Unchoke:
    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.UNCHOKE])


@dataclass(frozen=True, kw_only=True, slots=True)
class Interested:
    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.INTERESTED])


@dataclass(frozen=True, kw_only=True, slots=True)
class NotInterested:
    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.NOT_INTERESTED])


@dataclass(frozen=True, kw_only=True, slots=True)
class Have:
    piece_index: int

    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.HAVE]) + self.piece_index.to_bytes(4, byteorder="big")


@dataclass(frozen=True, kw_only=True, slots=True)
class Bitfield:
    bitfield: bitarray.bitarray

    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.BITFIELD]) + self.bitfield.tobytes()


@dataclass(frozen=True, kw_only=True, slots=True)
class Request:
    block: Block

    def to_bytes(self) -> bytes:
        return (
            bytes([MessageTypeByte.REQUEST])
            + self.block.piece_index.to_bytes(4, byteorder="big")
            + self.block.block_start.to_bytes(4, byteorder="big")
            + self.block.block_length.to_bytes(4, byteorder="big")
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Piece:
    peer_id: PeerId
    block: Block
    data: bytes

    def to_bytes(self) -> bytes:
        return (
            bytes([MessageTypeByte.PIECE])
            + self.block.piece_index.to_bytes(4, byteorder="big")
            + self.block.block_start.to_bytes(4, byteorder="big")
            + self.data
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Cancel:
    block: Block

    def to_bytes(self) -> bytes:
        return (
            bytes([MessageTypeByte.CANCEL])
            + self.block.piece_index.to_bytes(4, byteorder="big")
            + self.block.block_start.to_bytes(4, byteorder="big")
            + self.block.block_length.to_bytes(4, byteorder="big")
        )


PeerMessage: TypeAlias = (
    Choke | Unchoke | Interested | NotInterested | Have | Bitfield | Request | Piece | Cancel
)


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


def parse_piece(s: bytes) -> tuple[int, int, bytes]:
    index = int.from_bytes(s[:4], byteorder="big")
    begin = int.from_bytes(s[4:8], byteorder="big")
    data = s[8:]
    return (index, begin, data)


def parse_message(data: bytes, peer_id: PeerId) -> PeerMessage:
    """Parse a raw peer message (including type byte) into a PeerMessage.

    Args:
        data: The full message bytes including the message type byte.
        peer_id: The ID of the peer that sent this message (used for Piece messages).

    Raises:
        ValueError: If the message type is unknown.
    """
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
            return Have(piece_index=parse_have(payload))
        case MessageTypeByte.BITFIELD:
            return Bitfield(bitfield=parse_bitfield(payload))
        case MessageTypeByte.REQUEST:
            return Request(block=parse_request_or_cancel(payload))
        case MessageTypeByte.PIECE:
            index, begin, piece_data = parse_piece(payload)
            block = Block(piece_index=index, block_start=begin, block_length=len(piece_data))
            return Piece(peer_id=peer_id, block=block, data=piece_data)
        case MessageTypeByte.CANCEL:
            return Cancel(block=parse_request_or_cancel(payload))
        case _:
            raise ValueError(f"Unknown message type: {msg_type}")
