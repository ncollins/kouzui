import bitarray

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


DUMMY_PEER_ID = b"01234567890123456789"


def test_choke_round_trip() -> None:
    msg = Choke()
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, Choke)


def test_unchoke_round_trip() -> None:
    msg = Unchoke()
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, Unchoke)


def test_interested_round_trip() -> None:
    msg = Interested()
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, Interested)


def test_not_interested_round_trip() -> None:
    msg = NotInterested()
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, NotInterested)


def test_have_round_trip() -> None:
    msg = Have(piece_index=42)
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, Have)
    assert parsed.piece_index == 42


def test_bitfield_round_trip() -> None:
    pieces = bitarray.bitarray("10110100")
    msg = Bitfield(pieces=pieces)
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, Bitfield)
    assert parsed.pieces == pieces


def test_request_round_trip() -> None:
    block = Block(piece_index=5, block_start=0, block_length=8192)
    msg = Request(blocks={block})
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, Request)
    (parsed_block,) = parsed.blocks
    assert parsed_block == block


def test_request_multiple_blocks() -> None:
    blocks = {
        Block(piece_index=1, block_start=0, block_length=8192),
        Block(piece_index=1, block_start=8192, block_length=8192),
    }
    msg = Request(blocks=blocks)
    raw_list = msg.to_bytes()
    assert len(raw_list) == 2
    parsed_blocks = set()
    for raw in raw_list:
        parsed = parse_message(DUMMY_PEER_ID, raw)
        assert isinstance(parsed, Request)
        parsed_blocks.update(parsed.blocks)
    assert parsed_blocks == blocks


def test_piece_round_trip() -> None:
    block = Block(piece_index=3, block_start=16384, block_length=100)
    data = b"x" * 100
    msg = Piece(peer_id=DUMMY_PEER_ID, block=block, data=data)
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, Piece)
    assert parsed.block.piece_index == 3
    assert parsed.block.block_start == 16384
    assert parsed.data == data
    assert parsed.block.block_length == 100


def test_cancel_round_trip() -> None:
    block = Block(piece_index=7, block_start=0, block_length=8192)
    msg = Cancel(block=block)
    raw_list = msg.to_bytes()
    assert len(raw_list) == 1
    parsed = parse_message(DUMMY_PEER_ID, raw_list[0])
    assert isinstance(parsed, Cancel)
    assert parsed.block == block


def test_parse_message_unknown_type() -> None:
    try:
        parse_message(DUMMY_PEER_ID, bytes([99]))
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
