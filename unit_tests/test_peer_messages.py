from hypothesis import given, strategies as st

import bitarray

from peer_messages import (
    Choke,
    Have,
    Piece,
    Request,
    Unchoke,
    Bitfield,
    Interested,
    NotInterested,
    parse_message,
)
from shared_types import Block

ST_4_BYTE_UINT = st.integers(min_value=0, max_value=(2**8) ** 4 - 1)


def test_messages_with_no_payload() -> None:
    for message_type in (Choke, Unchoke, Interested, NotInterested):
        assert message_type() == parse_message(message_type().to_bytes())


@given(ST_4_BYTE_UINT)
def test_have(i: int) -> None:
    msg = Have(piece_index=i)
    assert msg == parse_message(msg.to_bytes())


@given(ST_4_BYTE_UINT, ST_4_BYTE_UINT, ST_4_BYTE_UINT)
def test_request(index: int, start: int, length: int) -> None:
    msg = Request(block=Block(piece_index=index, block_start=start, block_length=length))
    assert msg == parse_message(msg.to_bytes())


@given(st.binary(min_size=1, max_size=100))
def test_bitfield(data: bytes) -> None:
    bits = bitarray.bitarray(data)
    msg = Bitfield(pieces=bits)
    assert msg == parse_message(msg.to_bytes())


@given(ST_4_BYTE_UINT, ST_4_BYTE_UINT, st.binary(min_size=1, max_size=100))
def test_piece(index: int, start: int, data: bytes) -> None:
    msg = Piece(piece_index=index, block_start=start, data=data)
    assert msg == parse_message(msg.to_bytes())


def test_piece_str_short_data() -> None:
    data = b"hello"
    msg = Piece(piece_index=3, block_start=8192, data=data)
    assert str(msg) == f"Piece(piece_index=3, block_start=8192, data={data!s}>)"


def test_piece_str_long_data() -> None:
    data = b"x" * 20
    msg = Piece(piece_index=0, block_start=0, data=data)
    assert str(msg) == "Piece(piece_index=0, block_start=0, data=<length 20>>)"


def test_bitfield_str_short() -> None:
    bits = bitarray.bitarray("10110")
    msg = Bitfield(pieces=bits)
    assert str(msg) == f"Bitfield(pieces={bits!s})"


def test_bitfield_str_long() -> None:
    bits = bitarray.bitarray(20)
    bits.setall(0)
    msg = Bitfield(pieces=bits)
    assert str(msg) == "Bitfield(pieces=<length 20>)"
