import datetime
import logging
from typing import List, Dict, Tuple, Set

import peer_state

logger = logging.getLogger("requests")


class RequestManager(object):
    """
    Keeps track of blocks client requested by index, peer_address
    and block.
    """

    def __init__(self):
        self._requests: Set[
            Tuple[bytes, Tuple[int, int, int], datetime.datettime]
        ] = set()

    @property
    def size(self):
        return len(self._requests)

    def add_request(self, peer_id: bytes, block: Tuple[int, int, int]):
        self._requests.add((peer_id, block, datetime.datetime.now()))

    def delete_all_for_piece(self, index: int):
        to_delete = set((p_id, r, t) for p_id, r, t in self._requests if r[0] == index)
        logger.info(
            "Found {} block requests to delete for piece index {}".format(
                len(to_delete), index
            )
        )
        self._requests = set(
            (p_id, r, t) for p_id, r, t in self._requests if not r[0] == index
        )

    def delete_all_for_peer(self, peer_id: bytes):
        self._requests = set(
            (p_id, r, t) for p_id, r, t in self._requests if p_id != peer_id
        )

    def delete_all(self):
        self._requests = set()

    def delete_older_than(self, *, seconds: int) -> int:
        now = datetime.datetime.now()
        prev_len = len(self._requests)
        self._requests = set(
            (p_id, r, t) for p_id, r, t in self._requests if (now - t).seconds > seconds
        )
        new_len = len(self._requests)
        return prev_len - new_len

    def existing_requests_for_peer(self, peer_id: bytes) -> Set[Tuple[int, int, int]]:
        return set(r for p_id, r, _ in self._requests if p_id == peer_id)
