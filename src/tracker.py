import logging
from urllib import parse

import h11
import trio

from bencode import BencodeDict
from config import Config
import torrent
import http_stream
from shared_types import PeerAddress, PeerId

logger = logging.getLogger("tracker")


def _int2bytes(i: int) -> bytes:
    return b"%d" % i


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


def _replace_ipv6_lookback_with_localhost(address: PeerAddress) -> PeerAddress:
    if address.ip == b"::1":
        return PeerAddress(ip=b"localhost", port=address.port)
    else:
        return address


def parse_peers(
    data: bytes | list[BencodeDict], *, listening_port: int
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
        (_replace_ipv6_lookback_with_localhost(address), peer_id)
        for address, peer_id in peer_list
        if address.port != listening_port
    ]


def tracker_request(
    torrent_info: torrent.TorrentInfo,
    torrent_state: torrent.TorrentState,
    event: bytes | None,
) -> h11.Request:
    """
    Tracker request is an http GET request, sent with parameters telling
    the tracker about your client.
    """
    d = {
        b"info_hash": parse.quote_from_bytes(torrent_info.info_hash).encode(),
        b"peer_id": torrent_state.peer_id,
        # ip
        b"port": _int2bytes(torrent_state.listening_port),
        b"uploaded": _int2bytes(torrent_state.uploaded),
        b"downloaded": _int2bytes(torrent_state.downloaded),
        b"left": _int2bytes(torrent_state.left),
        # , b'event': event
        # , b'compact': b'1'
        b"compact": b"0",
        # testing
        # , b'supportcrypto': b'1'
        # , b'key': b'71c04610'
        # , b'numwant': b'80'
    }
    if event:
        d[b"event"] = event
    params = b"&".join([k + b"=" + v for k, v in d.items()])
    path = torrent_info.tracker_path + b"?" + params
    host = torrent_info.tracker_address + b":" + str(torrent_info.tracker_port).encode()
    headers = [
        ("Host", host.decode("utf-8")),  # has to be tuple[bytes, bytes] or tuple[str, str]
        ("Accept-Encoding", "gzip;q=1.0, deflate, identity"),
        ("Accept", "*/*"),
        ("User-Agent", "toytorrent"),
    ]
    r = h11.Request(method="GET", target=path, headers=headers)
    return r


async def query(
    torrent_info: torrent.TorrentInfo,
    torrent_state: torrent.TorrentState,
    event: bytes | None,
    *,
    cfg: Config,
) -> bytes:
    url: bytes = torrent_info.tracker_address
    port: int = torrent_info.tracker_port
    logger.debug(f"url/port = {url!r}/{port}")
    stream = await trio.open_tcp_stream(
        url.decode("ascii"), port
    )  # TODO fix hack with string/bytes issue
    logger.debug("Opened raw stream")
    h = http_stream.HttpStream(stream, h11.CLIENT, cfg=cfg)
    logger.debug("Created HttpStream")

    await h.send_event(tracker_request(torrent_info, torrent_state, event))
    await h.send_event(h11.EndOfMessage())

    _response, data = await h.receive_with_data()
    return b"".join(d.data for d in data if isinstance(d, h11.Data))
