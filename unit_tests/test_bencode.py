from bencode import parse_compact_peers


def test_parse_compact_peers():
    ip_addresses_and_ports = [
        ((192, 168, 1, 3), 50000),
        ((192, 168, 1, 4), 50001),
        ((192, 168, 1, 5), 50002),
    ]
    expected = [(b"192.168.1.3", 50000), (b"192.168.1.4", 50001), (b"192.168.1.5", 50002)]
    raw_bytes = b"".join(
        b"".join(n.to_bytes() for n in ip) + port.to_bytes(byteorder="big", length=2)
        for ip, port in ip_addresses_and_ports
    )
    assert expected == parse_compact_peers(raw_bytes)
