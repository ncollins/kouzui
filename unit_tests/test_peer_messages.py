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
