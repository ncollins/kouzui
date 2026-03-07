import bitarray
import pytest

from peer_messages import (
    Bitfield,
    Cancel,
    Choke,
    Have,
    Interested,
    NotInterested,
    Piece,
    Request,
    Unchoke,
    parse_message,
)
from shared_types import Block

PEER_ID = b"test_peer_id_123456789"[:20]


def test_choke_round_trip() -> None:
    msg = Choke()
    assert parse_message(msg.to_bytes(), PEER_ID) == msg


def test_unchoke_round_trip() -> None:
    msg = Unchoke()
    assert parse_message(msg.to_bytes(), PEER_ID) == msg


def test_interested_round_trip() -> None:
    msg = Interested()
    assert parse_message(msg.to_bytes(), PEER_ID) == msg


def test_not_interested_round_trip() -> None:
    msg = NotInterested()
    assert parse_message(msg.to_bytes(), PEER_ID) == msg


def test_have_round_trip() -> None:
    msg = Have(piece_index=42)
    assert parse_message(msg.to_bytes(), PEER_ID) == msg


def test_have_to_bytes() -> None:
    msg = Have(piece_index=1)
    data = msg.to_bytes()
    assert data == bytes([4]) + (1).to_bytes(4, byteorder="big")


def test_bitfield_round_trip() -> None:
    b = bitarray.bitarray([1, 0, 1, 1, 0, 0, 1, 0])
    msg = Bitfield(bitfield=b)
    result = parse_message(msg.to_bytes(), PEER_ID)
    assert isinstance(result, Bitfield)
    assert result.bitfield == msg.bitfield


def test_request_round_trip() -> None:
    block = Block(piece_index=3, block_start=8192, block_length=8192)
    msg = Request(block=block)
    assert parse_message(msg.to_bytes(), PEER_ID) == msg


def test_request_to_bytes() -> None:
    block = Block(piece_index=1, block_start=0, block_length=8192)
    msg = Request(block=block)
    data = msg.to_bytes()
    assert data[0] == 6  # REQUEST type byte
    assert int.from_bytes(data[1:5], byteorder="big") == 1
    assert int.from_bytes(data[5:9], byteorder="big") == 0
    assert int.from_bytes(data[9:13], byteorder="big") == 8192


def test_piece_round_trip() -> None:
    block = Block(piece_index=5, block_start=0, block_length=8)
    msg = Piece(peer_id=PEER_ID, block=block, data=b"testdata")
    assert parse_message(msg.to_bytes(), PEER_ID) == msg


def test_piece_to_bytes_excludes_peer_id() -> None:
    block = Block(piece_index=2, block_start=16384, block_length=4)
    msg = Piece(peer_id=PEER_ID, block=block, data=b"abcd")
    data = msg.to_bytes()
    assert data[0] == 7  # PIECE type byte
    assert int.from_bytes(data[1:5], byteorder="big") == 2
    assert int.from_bytes(data[5:9], byteorder="big") == 16384
    assert data[9:] == b"abcd"


def test_cancel_round_trip() -> None:
    block = Block(piece_index=2, block_start=0, block_length=4096)
    msg = Cancel(block=block)
    assert parse_message(msg.to_bytes(), PEER_ID) == msg


def test_parse_message_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="Unknown message type"):
        parse_message(bytes([99]), PEER_ID)
