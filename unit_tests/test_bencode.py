from collections import OrderedDict
from unittest.mock import MagicMock

from bencode import parse_compact_peers, parse_peers
from utility_types import PeerAddress


def test_parse_compact_peers():
    ip_addresses_and_ports = [
        ((192, 168, 1, 3), 50000),
        ((192, 168, 1, 4), 50001),
        ((192, 168, 1, 5), 50002),
    ]
    expected = [
        PeerAddress(ip=b"192.168.1.3", port=50000),
        PeerAddress(ip=b"192.168.1.4", port=50001),
        PeerAddress(ip=b"192.168.1.5", port=50002),
    ]
    raw_bytes = b"".join(
        b"".join(n.to_bytes() for n in ip) + port.to_bytes(byteorder="big", length=2)
        for ip, port in ip_addresses_and_ports
    )
    assert expected == parse_compact_peers(raw_bytes)


def test_parse_peers():
    torrent = MagicMock()
    torrent.listening_port = 60000
    input = [
        OrderedDict(
            [(b"ip", b"127.0.0.1"), (b"peer id", b"CeDTEzzTR6pcjYKYAzLJ"), (b"port", 50000)]
        ),
        OrderedDict(
            [(b"ip", b"127.0.0.1"), (b"peer id", b"eQy4S9UbWLyKFBU99Il4"), (b"port", 50001)]
        ),
        OrderedDict(
            [(b"ip", b"127.0.0.1"), (b"peer id", b"4GX6PF3lK4PzfNzmiV8x"), (b"port", 50002)]
        ),
    ]
    expected = [
        (PeerAddress(ip=b"127.0.0.1", port=50000), b"CeDTEzzTR6pcjYKYAzLJ"),
        (PeerAddress(ip=b"127.0.0.1", port=50001), b"eQy4S9UbWLyKFBU99Il4"),
        (PeerAddress(ip=b"127.0.0.1", port=50002), b"4GX6PF3lK4PzfNzmiV8x"),
    ]
    assert expected == parse_peers(input, torrent)
