from collections.abc import Iterable
import datetime
import logging
import random
from typing import Set

import bitarray

import config
from shared_types import Block, PeerId
from torrent import TorrentInfo
from peer_state import PeerState

logger = logging.getLogger("requests")


def _pick_random_one_in_bitarray(b: bitarray.bitarray) -> int | None:
    """
    For a bitarary, b, this picks a random index, i, such that
    b[i] == 1.

    It does this by picking a random starting index and searching forwards
    until it finds an entry equal to 1. If that fails then it searches
    backwards from the starting index.

    Returns None if it can't find an element equal to 1.

    >>> _pick_random_one_in_bitarray(bitarray.bitarray([1,0,0,0]))
    0
    >>> _pick_random_one_in_bitarray(bitarray.bitarray([0,0,0,1]))
    3
    >>> _pick_random_one_in_bitarray(bitarray.bitarray([0,0,0,0])) is None
    True
    """
    n = len(b)
    start = random.randint(0, n - 1)
    # look at tail
    try:
        i = b.index(True, start)
        return i
    except ValueError:
        pass
    # look at head
    try:
        i = b.index(True, 0, start)
        return i
    except ValueError:
        return None


class RequestManager(object):
    """
    Keeps track of blocks client requested by index, peer_address
    and block.
    """

    def __init__(self, *, torrent_info: TorrentInfo, cfg: config.Config) -> None:
        self._torrent_info = torrent_info
        self._cfg = cfg
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

    def __delete_all_for_peer(self, peer_id: PeerId) -> None:
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

    def _blocks_from_index(self, index: int) -> set[Block]:
        piece_length = self._torrent_info.piece_length(index)
        block_length = min(piece_length, self._cfg.block_size)
        begin_indexes = list(range(0, piece_length, block_length))
        return set(
            Block(
                piece_index=index,
                block_start=begin,
                block_length=min(block_length, piece_length - begin),
            )
            for begin in begin_indexes
        )

    def update_peer_requests(
        self,
        *,
        completed_pieces: bitarray.bitarray,
        peers: Iterable[tuple[PeerId, PeerState]],
    ) -> Iterable[tuple[PeerId, Block]]:
        new_requests: set[tuple[PeerId, Block]] = set()
        for peer_id, peer_state in peers:
            if peer_state.is_client_choked:
                logger.info(f"No new requests for {peer_id!r} as we are choked")
                continue

            existing_requests = self.existing_requests_for_peer(peer_id)
            if len(existing_requests) > self._cfg.max_outstanding_requests_per_peer:
                logger.info(f"No new requests for {peer_id!r}: {len(existing_requests)} existing")
            else:
                targets = (~completed_pieces) & peer_state._pieces
                target_index = _pick_random_one_in_bitarray(targets)
                if target_index is None:
                    logger.info(f"No target pieces for {peer_id!r}")
                else:
                    suggested_requests_for_peer = self._blocks_from_index(target_index)
                    new_requests_for_peer = suggested_requests_for_peer.difference(
                        existing_requests
                    )
                    logger.info(
                        f"{peer_id!r}: new_requests = <length={len(new_requests_for_peer)}>"
                    )
                    for block in new_requests_for_peer:
                        self.add_request(peer_id, block)
                        new_requests.add((peer_id, block))
        return new_requests
