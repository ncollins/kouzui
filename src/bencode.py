# encode and decode the Bittorrent serialization format
# http://www.bittorrent.org/beps/bep_0003.html

from __future__ import annotations

import collections
import io
from typing import Any, BinaryIO, TYPE_CHECKING


if TYPE_CHECKING:
    import torrent


def parse_string_length(s: BinaryIO, i: bytes = b"") -> int:
    # Take string from the front and return the rest
    c = s.read(1)
    while c.isdigit():
        i += c
        c = s.read(1)
    # c should be ':' here
    if c == b":":
        return int(i)
    else:
        raise Exception("String length should be terminated by ':'")


def parse_string(s: BinaryIO, n: int) -> bytes:
    return s.read(n)


def parse_int(s: BinaryIO) -> int:
    i = b""
    c = s.read(1)
    while c.isdigit():
        i += c
        c = s.read(1)
    # c should be 'e' here
    if c == b"e":
        return int(i)
    else:
        raise Exception("Integer not terminated by 'e'")


def parse_list(s: BinaryIO) -> list[Any]:
    l: list = []
    while True:
        v = parse_value(s)
        if v is None:
            return l
        else:
            l.append(v)


def parse_dict(s: BinaryIO) -> dict[bytes, Any]:
    d: dict = collections.OrderedDict()
    while True:
        k = parse_value(s)
        if k is None:
            return d
        else:
            v = parse_value(s)
            d[k] = v


def parse_value(s: BinaryIO) -> int | bytes | list[Any] | dict[bytes, Any] | None:
    # `s` is a string stream object
    # look at first character
    # digit -> string
    # 'i'   -> integer
    # 'l'   -> list
    # 'd'   -> dictionary
    c = s.read(1)
    if c.isdigit():
        length = parse_string_length(s, c)
        return parse_string(s, length)
    elif c == b"i":
        return parse_int(s)
    elif c == b"l":
        return parse_list(s)
    elif c == b"d":
        return parse_dict(s)
    elif c == b"e" or c == "":
        return None
    else:
        raise Exception("Expected a digit, 'i', 'l', or 'd'. Got {!r}".format(c))


# def parse(s):
#    stream = io.StringIO(s)
#    return parse_value(stream)

# print(parse('l8:abcdefgh4:spamdi10e11:abcdefghijke'))

# def extract_info(s):
#    c = s.read(1)
#    if c != b'd':
#        raise Exception("Need a dictionary to get 'info' string")
#    d = dict()
#    while True:
#        k = parse_value(s)
#        if k == b'info':
#            return parse_value(s)
#        else:
#            _ = parse_value(s)
#    raise Exception("No 'info' key found")


def encode_bytes(s: bytes) -> bytes:
    return b"%d:%s" % (len(s), s)


def encode_string(s: str) -> bytes:
    return encode_bytes(s.encode())


def encode_int(i: int) -> bytes:
    return b"i%de" % i


def encode_list(l: list[Any]) -> bytes:
    inner = b"".join(encode_value(v) for v in l)
    return b"l%se" % inner


def encode_dict(d: dict[bytes, Any]) -> bytes:
    inner = b"".join(encode_value(k) + encode_value(v) for k, v in d.items())
    return b"d%se" % inner


def encode_value(v: int | bytes | str | list[Any] | dict[bytes, Any]) -> bytes:
    if isinstance(v, bytes):
        return encode_bytes(v)
    elif isinstance(v, str):
        return encode_string(v)
    elif isinstance(v, int):
        return encode_int(v)
    elif isinstance(v, list):
        return encode_list(v)
    elif isinstance(v, dict):
        return encode_dict(v)
    else:
        raise Exception('Unsupported type for bencoding: "{}"'.format(type(v)))


def parse_compact_peers(raw_bytes: bytes) -> list[tuple[bytes, int]]:
    if (len(raw_bytes) % 6) != 0:
        raise Exception("Peer list length is not a multiple of 6.")
    else:
        peers = []
        for i in range(0, len(raw_bytes), 6):
            ip = ".".join(str(x) for x in raw_bytes[i : i + 4]).encode()
            port = int.from_bytes(raw_bytes[i + 4 : i + 6], byteorder="big")
            peers.append((ip, port))
        return peers


def replace_with_localhost(
    tripple: tuple[bytes, int, bytes | None],
) -> tuple[bytes, int, bytes | None]:
    if tripple[0] == b"::1":
        return (b"localhost", tripple[1], tripple[2])
    else:
        return tripple


def parse_peers(data: bytes, torrent: torrent.Torrent) -> list[tuple[bytes, int, bytes | None]]:
    # TODO this try/except logic probably shouldn't be here as it's not really
    # a bencode issue
    try:
        peer_list = [(ip, port, None) for ip, port in parse_compact_peers(data)]
    except:
        peer_list = [(x[b"ip"], x[b"port"], x[b"peer id"]) for x in data]  # type: ignore
    return [
        replace_with_localhost(tripple)
        for tripple in peer_list
        if tripple[1] != torrent.listening_port
    ]
