import datetime
import hashlib
import itertools
import logging
import os
import random
import re
from typing import NamedTuple, Any, List, Dict, Tuple, Optional, Set

import bitarray
import trio

from config import DEFAULT_LISTENING_PORT

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


Piece = NamedTuple("Piece", [("filename", str), ("index", int), ("sha1hash", bytes)])


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


def _generate_peer_id() -> bytes:
    return "".join(_random_char() for _ in range(0, 20)).encode()


def _parse_pieces(bstring: bytes) -> List[bytes]:
    if (len(bstring) % 20) != 0:
        raise Exception("'pieces' is not a multiple of 20'")
    else:
        l: List[bytes] = []
        i = 0
        while i + 20 <= len(bstring):
            l.append(bstring[i : i + 20])
            i += 20
        return l


class Torrent(object):
    """
    The Torrent object stores all information about an active torrent.
    It is initiallised with the dictionary values taken from the
    .torrent file. It then takes data from the tracker and peers.

    None of the methods are async, but it can trigger async events by pushing
    messages into a trio.Queue, which can be used in a blocking or non-blocking
    fashion.
    """

    def __init__(self, tdict, info_string, directory, listening_port=None, custom_name=None):
        self._listening_port = listening_port
        self._info_string = info_string
        self._info_hash = hashlib.sha1(info_string).digest()
        self._peer_id = _generate_peer_id()
        self._uploaded = 0
        self._downloaded = 0
        self._piece_length = int(tdict[b"info"][b"piece length"])
        if b"files" in tdict[b"info"]:  # multi-file case
            raise Exception("multi-file torrents not yet supported")
        else:  # single file case
            # store hash and a bolean to mark if we have the piece or not
            self._torrent_name = bytes.decode(tdict[b"info"][b"name"])
            if custom_name:
                self._filename = os.path.join(directory, custom_name)
            else:
                self._filename = os.path.join(directory, self._torrent_name)

            self._pieces = [
                Piece(self._filename, i, sha1)
                for i, sha1 in enumerate(_parse_pieces(tdict[b"info"][b"pieces"]))
            ]

            self._file_length = int(tdict[b"info"][b"length"])
            self._left = self._file_length

            self._num_pieces = len(self._pieces)
            self._complete = bitarray.bitarray(self._num_pieces)
            self._complete.setall(False)

        # deconstruct url
        self._raw_tracker_url = tdict[b"announce"]
        r = re.compile(r"(?P<http>http://)?(?P<address>.+):(?P<port>\d+)(?P<path>.+)")
        m = r.fullmatch(self._raw_tracker_url.decode())
        g = m.groupdict()
        self._tracker_address = m["address"].encode()
        self._tracker_port = int(m["port"])
        self._tracker_path = m["path"].encode()
        logger.info(
            "Tracker address: {}, port: {}, path: {}".format(
                self._tracker_address, self._tracker_port, self._tracker_path
            )
        )

        # info not from .torrent file
        self._interval = 100
        self._complete_peers = 0
        self._incomplete_peers = 0

    @property
    def listening_port(self):
        if self._listening_port:
            return self._listening_port
        else:
            return DEFAULT_LISTENING_PORT

    @property
    def file_path(self):
        return self._filename

    def piece_length(self, index: int) -> int:
        last_piece = self._num_pieces - 1
        if index < last_piece:
            return self._piece_length
        else:
            return min(self._piece_length, self._file_length - self._piece_length * last_piece)

    @property
    def info_hash(self):
        return self._info_hash

    @property
    def peer_id(self):
        return self._peer_id

    @property
    def interval(self):
        return self._interval

    @property
    def tracker_address(self) -> bytes:
        return self._tracker_address

    @property
    def tracker_port(self) -> int:
        return self._tracker_port

    @property
    def tracker_path(self):
        return self._tracker_path

    @property
    def uploaded(self):
        # TODO this needs to update while we run
        return 0

    @property
    def downloaded(self):
        # TODO this needs to update while we run
        return 0

    @property
    def left(self):
        # TODO this needs to update while we run
        return self._file_length

    def piece_info(self, n: int) -> Piece:
        return self._pieces[n]

    def is_piece_complete(self, index):
        return self._complete[index]
