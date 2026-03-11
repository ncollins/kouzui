import hashlib
import logging
from pathlib import Path
import random
import re
from dataclasses import dataclass
from typing import NamedTuple, cast

import bitarray

import bencode
from shared_types import PeerId


logger = logging.getLogger("torrent")

# Key information in torrent dictionary, d:
#
# d['announce'] -> the url of the tracker
#
# d['info']['name'] -> suggested file or directory name
#
# d['info']['pieces'] -> string with length that's a multiple of 20, each 20 byte
# section is the SHA1 hash of of the entry at that index
#
# d['info']['piece length'] -> number of bytes of each piece, with the
# exception of the last one (may be shorter)
#
# d['info']['length'] -> if single file, the length in bytes
# OR
# d['info']['files'] -> if multiple files, a list of dictionaries with
# 'length' and 'path' keys


PieceInfo = NamedTuple("PieceInfo", [("index", int), ("sha1hash", bytes)])


def _random_char() -> str:
    # ASCII ranges
    # 65-90: A-Z
    # 97-122: a-z
    # 48-57: 0-9
    n = random.randint(0, 61)
    if n < 26:
        c = chr(n + 65)
    elif n < 52:
        c = chr(n - 26 + 97)
    else:
        c = chr(n - 52 + 48)
    return c


def generate_peer_id() -> PeerId:
    return "".join(_random_char() for _ in range(0, 20)).encode()


def _parse_pieces(bstring: bytes) -> list[bytes]:
    if (len(bstring) % 20) != 0:
        raise Exception("'pieces' is not a multiple of 20'")
    else:
        pieces: list[bytes] = []
        i = 0
        while i + 20 <= len(bstring):
            pieces.append(bstring[i : i + 20])
            i += 20
        return pieces


@dataclass(frozen=True, kw_only=True)
class TorrentInfo:
    torrent_name: str
    info_hash: bytes
    interval: int
    tracker_address: bytes
    tracker_port: int
    tracker_path: bytes
    pieces: list[PieceInfo]
    file_length: int
    num_pieces: int
    piece_size: int

    def piece_length(self, index: int) -> int:
        last_piece = self.num_pieces - 1
        if index < last_piece:
            return self.piece_size
        else:
            return min(self.piece_size, self.file_length - self.piece_size * last_piece)

    def piece_info(self, n: int) -> PieceInfo:
        return self.pieces[n]


@dataclass(kw_only=True)
class TorrentState:
    uploaded: int = 0
    downloaded: int = 0
    left: int
    file_path: Path
    listening_port: int
    peer_id: PeerId
    completed_pieces: bitarray.bitarray


def parse_torrent_dict(
    tdict: bencode.BencodeDict,
) -> TorrentInfo:
    # we need to re-encode the info dictionary to get the sha1 hash
    info_dict = cast(bencode.BencodeDict, tdict[b"info"])
    info_string = bencode.encode_value(info_dict)
    logger.debug(f"info_string = {info_string!r}")
    info_hash = hashlib.sha1(info_string).digest()
    piece_size = int(info_dict[b"piece length"])

    if b"files" in info_dict:  # multi-file case
        raise Exception("multi-file torrents not yet supported")

    torrent_name = bytes.decode(info_dict[b"name"])

    pieces = [PieceInfo(i, sha1) for i, sha1 in enumerate(_parse_pieces(info_dict[b"pieces"]))]

    file_length = int(info_dict[b"length"])
    num_pieces = len(pieces)

    raw_tracker_url = tdict[b"announce"]
    r = re.compile(r"(?P<http>http://)?(?P<address>.+):(?P<port>\d+)(?P<path>.+)")
    m = r.fullmatch(raw_tracker_url.decode())
    if m is None:
        raise Exception(f"Unable to parse tracker URL: {raw_tracker_url.decode()}")
    tracker_address: bytes = m["address"].encode()
    tracker_port: int = int(m["port"])
    tracker_path: bytes = m["path"].encode()
    logger.info(
        f"Tracker address: {tracker_address!r}, port: {tracker_port}, path: {tracker_path!r}"
    )

    return TorrentInfo(
        torrent_name=torrent_name,
        info_hash=info_hash,
        interval=100,
        tracker_address=tracker_address,
        tracker_port=tracker_port,
        tracker_path=tracker_path,
        pieces=pieces,
        file_length=file_length,
        num_pieces=num_pieces,
        piece_size=piece_size,
    )
