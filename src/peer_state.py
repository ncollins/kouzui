import datetime
from enum import Enum
from typing import NamedTuple, Tuple, Set

import bitarray
import trio

PeerAddress = NamedTuple('PeerAddress', [('ip', bytes), ('port', int)])

class PeerType(Enum):
    SERVER = 0
    CLIENT = 1

class ChokeAlert(Enum):
    ALERT = 0
    DONT_ALERT = 1

class PeerState(object):
    def __init__(self, peer_id: bytes, num_pieces: int) -> None:
        now = datetime.datetime.now()
        pieces = bitarray.bitarray(num_pieces)
        pieces.setall(False)
        self._pieces = pieces
        self._peer_id = peer_id
        self._to_send_queue = trio.Queue(100) # TODO remove magic number
        self._choked_us = True
        self._choked_them = True
        # stats
        self._first_seen = now
        self._last_seen = now
        self._total_download_count = 0
        self._current_10_second_download_count = 0
        self._prev_10_second_download_count = 0
        self._total_upload_count = 0

    def choke_us(self):
        self._choked_us = True

    def unchoke_us(self):
        self._choked_us = False

    @property
    def is_client_choked(self):
        return self._choked_us

    def choke_them(self) -> ChokeAlert:
        if self._choked_them:
            return ChokeAlert.DONT_ALERT
        else:
            self._choked_them = True
            return ChokeAlert.ALERT

    def unchoke_them(self) -> ChokeAlert:
        if not self._choked_them:
            return ChokeAlert.DONT_ALERT
        else:
            self._choked_them = False
            return ChokeAlert.ALERT

    @property
    def is_peer_choked(self):
        return self._choked_them

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

    @property
    def to_send_queue(self) -> trio.Queue:
        return self._to_send_queue

    def inc_download_counters(self) -> None:
        self._total_download_count += 1
        self._current_10_second_download_count += 1

    def inc_upload_counters(self) -> None:
        self._total_upload_count += 1

    def reset_rolling_download_count(self) -> None:
        self._prev_10_second_download_count = self._current_10_second_download_count
        self._current_10_second_download_count = 0

    def get_20_second_rolling_download_count(self) -> int:
        return self._prev_10_second_download_count + self._current_10_second_download_count
