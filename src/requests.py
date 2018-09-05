import logging
from typing import List, Dict, Tuple, Set

import peer_state

logger = logging.getLogger('requests')

class RequestManager(object):
    '''
    Keeps track of blocks client requested by index, peer_address
    and block.
    '''
    def __init__(self):
        self._requests: Set[Tuple[bytes,Tuple[int,int,int]]] = set()

    @property
    def size(self):
        return len(self._requests)

    def add_request(self, peer_id: bytes, block: Tuple[int,int,int]):
        self._requests.add((peer_id, block))

    def delete_all_for_piece(self, index: int):
        to_delete = set((p_id, r) for p_id, r in self._requests if r[0] == index)
        logger.info('Found {} block requests to delete for piece index {}'.format(len(to_delete),index))
        self._requests = set((p_id, r) for p_id, r in self._requests if not r[0] == index)

    def delete_all_for_peer(self, peer_id: bytes):
        self._requests = set((p_id, r) for p_id, r in self._requests if p_id != peer_id)

    def delete_all(self):
        self._requests = set()

    def existing_requests_for_peer(self, peer_id: bytes) -> Set[Tuple[int,int,int]]:
        return set(r for p_id, r in self._requests if p_id == peer_id)
