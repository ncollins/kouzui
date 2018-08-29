import datetime
from enum import Enum
from typing import NamedTuple, Tuple, Set

import bitarray
import trio

PeerAddress = NamedTuple('PeerAddress', [('ip', bytes), ('port', int)])

class PeerType(Enum):
    SERVER = 0
    CLIENT = 1

class PeerState(object):
    def __init__(self, pieces: bitarray.bitarray, first_seen: datetime.datetime, peer_id = None) -> None:
        self._pieces = pieces
        self._first_seen = first_seen
        self._last_seen = first_seen
        self._peer_id = peer_id
        self._requested: Set[Tuple[int,int,int]] = set()
        self._to_send_queue = trio.Queue(100) # TODO remove magic number

    def add_request(self, request_info: Tuple[int,int,int]) -> None:
        self._requested.add(request_info)

    def cancel_request(self, request_info: Tuple[int,int,int]) -> None:
        self._requested.discard(request_info)

    def get_pieces(self):
        return self._pieces

    def set_pieces(self, new_pieces):
        # crop the new pieces because peers send data
        # in complete bytes
        length = len(self._pieces)
        self._pieces = new_pieces[:length]

    @property
    def first_seen(self):
        return self._first_seen

    @property
    def last_seen(self):
        return self._last_seen

    @property
    def peer_id(self):
        return self._peer_id

    def set_peer_id(self, peer_id):
        self._peer_id = peer_id

    @property
    def to_send_queue(self) -> trio.Queue:
        return self._to_send_queue

