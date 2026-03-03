# encode and decode the Bittorrent serialization format
# http://www.bittorrent.org/beps/bep_0003.html

from __future__ import annotations

import collections
from typing import Any, BinaryIO, TYPE_CHECKING


if TYPE_CHECKING:
    import torrent
from shared_types import PeerAddress, PeerId


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
    xs: list = []
    while True:
        v = parse_value(s)
        if v is None:
            return xs
        else:
            xs.append(v)


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
        raise Exception(f"Expected a digit, 'i', 'l', or 'd'. Got {c!r}")


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


def encode_list(xs: list[Any]) -> bytes:
    inner = b"".join(encode_value(x) for x in xs)
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
        raise Exception(f'Unsupported type for bencoding: "{type(v)}"')


def parse_compact_peers(raw_bytes: bytes) -> list[PeerAddress]:
    if (len(raw_bytes) % 6) != 0:
        raise Exception("Peer list length is not a multiple of 6.")
    else:
        peers = []
        for i in range(0, len(raw_bytes), 6):
            ip = ".".join(str(x) for x in raw_bytes[i : i + 4]).encode()
            port = int.from_bytes(raw_bytes[i + 4 : i + 6], byteorder="big")
            peers.append(PeerAddress(ip=ip, port=port))
        return peers


def replace_ipv6_lookback_with_localhost(address: PeerAddress) -> PeerAddress:
    if address.ip == b"::1":
        # return dataclasses.replace(address, ip=b"localhost")
        return PeerAddress(ip=b"localhost", port=address.port)
    else:
        return address


def parse_peers(
    data: bytes | list[collections.OrderedDict[bytes, Any]], torrent: torrent.Torrent
) -> list[tuple[PeerAddress, PeerId | None]]:
    peer_list: list[tuple[PeerAddress, PeerId | None]] = []
    match data:
        case bytes():
            peer_list = [(address, None) for address in parse_compact_peers(data)]
        case list():
            peer_list = [
                (PeerAddress(ip=x[b"ip"], port=x[b"port"]), PeerId(x[b"peer id"])) for x in data
            ]
    return [
        (replace_ipv6_lookback_with_localhost(address), peer_id)
        for address, peer_id in peer_list
        if address.port != torrent.listening_port
    ]
