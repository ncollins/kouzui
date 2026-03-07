import abc
from dataclasses import dataclass
from enum import IntEnum

import bitarray
import trio

from shared_types import Block


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
class PeerMessage(abc.ABC):
    @abc.abstractmethod
    def to_bytes(self) -> bytes:
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True, slots=True)
class Choke(PeerMessage):
    pass

    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.CHOKE])


@dataclass(frozen=True, kw_only=True, slots=True)
class Unchoke(PeerMessage):
    pass

    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.UNCHOKE])


@dataclass(frozen=True, kw_only=True, slots=True)
class Interested(PeerMessage):
    pass

    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.INTERESTED])


@dataclass(frozen=True, kw_only=True, slots=True)
class NotInterested(PeerMessage):
    pass

    def to_bytes(self) -> bytes:
        return bytes([MessageTypeByte.NOT_INTERESTED])


@dataclass(frozen=True, kw_only=True, slots=True)
class Have(PeerMessage):
    piece_index: int

    def to_bytes(self) -> bytes:
        raw_msg = bytes([MessageTypeByte.HAVE])
        raw_msg += (self.piece_index).to_bytes(4, byteorder="big")
        return raw_msg


@dataclass(frozen=True, kw_only=True, slots=True)
class Bitfield(PeerMessage):
    pieces: bitarray.bitarray

    def to_bytes(self) -> bytes:
        raw_msg = bytes([MessageTypeByte.BITFIELD])
        raw_msg += self.pieces.tobytes()
        return raw_msg


@dataclass(frozen=True, kw_only=True, slots=True)
class Request(PeerMessage):
    block: Block

    def to_bytes(self) -> bytes:
        raw_msg = bytes([MessageTypeByte.REQUEST])
        raw_msg += (self.block.piece_index).to_bytes(4, byteorder="big")
        raw_msg += (self.block.block_start).to_bytes(4, byteorder="big")
        raw_msg += (self.block.block_length).to_bytes(4, byteorder="big")
        return raw_msg


@dataclass(frozen=True, kw_only=True, slots=True)
class Piece(PeerMessage):
    piece_index: int
    block_start: int
    data: bytes

    def to_bytes(self) -> bytes:
        raw_msg = bytes([MessageTypeByte.PIECE])
        raw_msg += (self.piece_index).to_bytes(4, byteorder="big")
        raw_msg += (self.block_start).to_bytes(4, byteorder="big")
        raw_msg += self.data
        return raw_msg


@dataclass(frozen=True, kw_only=True, slots=True)
class Cancel(PeerMessage):
    block: Block

    def to_bytes(self) -> bytes:
        raw_msg = bytes([MessageTypeByte.CANCEL])
        raw_msg += (self.block.piece_index).to_bytes(4, byteorder="big")
        raw_msg += (self.block.block_start).to_bytes(4, byteorder="big")
        raw_msg += (self.block.block_length).to_bytes(4, byteorder="big")
        return raw_msg


def _parse_have(s: bytes) -> int:
    return int.from_bytes(s[:4], byteorder="big")


def _parse_bitfield(s: bytes) -> bitarray.bitarray:
    # NOTE the input will be an integer number of bytes, so it may
    # have extra bits
    b = bitarray.bitarray()
    b.frombytes(s)
    return b


def _parse_request_or_cancel(s: bytes) -> Block:
    # This should be 12 bytes in most cases, so I'm hardcoding it for now.
    return Block(
        piece_index=int.from_bytes(s[:4], byteorder="big"),
        block_start=int.from_bytes(s[4:8], byteorder="big"),
        block_length=int.from_bytes(s[8:], byteorder="big"),
    )


def _parse_piece(s: bytes) -> tuple[int, int, bytes]:
    index = int.from_bytes(s[:4], byteorder="big")
    begin = int.from_bytes(s[4:8], byteorder="big")
    data = s[8:]
    return (index, begin, data)


def parse_message(msg: bytes) -> PeerMessage:
    msg_type = msg[0]
    msg_payload = msg[1:]
    match msg_type:
        case MessageTypeByte.CHOKE:
            assert len(msg_payload) == 0
            return Choke()
        case MessageTypeByte.UNCHOKE:
            assert len(msg_payload) == 0
            return Unchoke()
        case MessageTypeByte.INTERESTED:
            assert len(msg_payload) == 0
            return Interested()
        case MessageTypeByte.NOT_INTERESTED:
            assert len(msg_payload) == 0
            return NotInterested()
        case MessageTypeByte.HAVE:
            return Have(piece_index=_parse_have(msg_payload))
        case MessageTypeByte.BITFIELD:
            pieces = _parse_bitfield(msg_payload)
            return Bitfield(pieces=pieces)
        case MessageTypeByte.REQUEST:
            block = _parse_request_or_cancel(msg_payload)
            return Request(block=block)
        case MessageTypeByte.PIECE:
            (index, begin, data) = _parse_piece(msg_payload)
            return Piece(piece_index=index, block_start=begin, data=data)
        case MessageTypeByte.CANCEL:
            block = _parse_request_or_cancel(msg_payload)
            return Cancel(block=block)
        case _:
            raise ValueError(f"{msg_type!r} is not a valid MessageTypeByte")


@dataclass(frozen=True, kw_only=True, slots=True)
class CloseConnectionOrder:
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class PeerConnectionStatus(abc.ABC):
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class PeerHandshakeSuccess(PeerConnectionStatus):
    peer_channel: trio.MemorySendChannel[PeerMessage | CloseConnectionOrder]


@dataclass(frozen=True, kw_only=True, slots=True)
class PeerConnectionShutdown(PeerConnectionStatus):
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class PeerConnectionError(PeerConnectionStatus):
    exception: Exception
