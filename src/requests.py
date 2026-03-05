import datetime
import logging
from typing import Set

from shared_types import Block, PeerId

logger = logging.getLogger("requests")


class RequestManager(object):
    """
    Keeps track of blocks client requested by index, peer_address
    and block.
    """

    def __init__(self):
        self._requests: Set[tuple[PeerId, Block, datetime.datetime]] = set()

    @property
    def size(self) -> int:
        return len(self._requests)

    def add_request(self, peer_id: PeerId, block: Block) -> None:
        self._requests.add((peer_id, block, datetime.datetime.now()))

    def delete_all_for_piece(self, index: int) -> None:
        to_delete = set((p_id, r, t) for p_id, r, t in self._requests if r.piece_index == index)
        logger.info(f"Found {len(to_delete)} block requests to delete for piece index {index}")
        self._requests = set(
            (p_id, r, t) for p_id, r, t in self._requests if r.piece_index != index
        )

    def delete_all_for_peer(self, peer_id: PeerId) -> None:
        self._requests = set((p_id, r, t) for p_id, r, t in self._requests if p_id != peer_id)

    def delete_all(self) -> None:
        self._requests = set()

    def delete_older_than(self, *, seconds: int) -> int:
        now = datetime.datetime.now()
        prev_len = len(self._requests)
        self._requests = set(
            (p_id, r, t) for p_id, r, t in self._requests if (now - t).seconds > seconds
        )
        new_len = len(self._requests)
        return prev_len - new_len

    def existing_requests_for_peer(self, peer_id: PeerId) -> Set[Block]:
        return set(r for p_id, r, _ in self._requests if p_id == peer_id)
