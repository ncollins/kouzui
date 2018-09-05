from enum import IntEnum
from typing import Tuple

import bitarray

class PeerMsg(IntEnum):
    CHOKE          = 0
    UNCHOKE        = 1
    INTERESTED     = 2
    NOT_INTERESTED = 3
    HAVE           = 4
    BITFIELD       = 5
    REQUEST        = 6
    PIECE          = 7
    CANCEL         = 8

def parse_have(s: bytes) -> int:
    return int.from_bytes(s[:4], byteorder='big')

def parse_bitfield(s: bytes) -> bitarray:
    # NOTE the input will be an integer number of bytes, so it may
    # have extra bits
    b = bitarray.bitarray()
    b.frombytes(s)
    return b

def parse_request_or_cancel(s: bytes) -> Tuple[int,int,int]:
    # This should be 12 bytes in most cases, so I'm hardcoding it for now.
    index = int.from_bytes(s[:4], byteorder='big')
    begin = int.from_bytes(s[4:8], byteorder='big')
    length = int.from_bytes(s[8:], byteorder='big')
    return (index, begin, length)

def parse_piece(s):
    index = int.from_bytes(s[:4], byteorder='big')
    begin = int.from_bytes(s[4:8], byteorder='big')
    data = s[8:]
    return (index, begin, data)
