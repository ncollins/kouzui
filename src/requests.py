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
        self._requests: Set[Tuple[peer_state.PeerAddress,Tuple[int,int,int]]] = set()

    @property
    def size(self):
        return len(self._requests)

    def add_request(self, peer_address: peer_state.PeerAddress, block: Tuple[int,int,int]):
        self._requests.add((peer_address, block))

    def delete_all_for_piece(self, index: int):
        to_delete = set((a, r) for a, r in self._requests if r[0] == index)
        logger.info('Found {} block requests to delete for piece index {}'.format(len(to_delete),index))
        self._requests = set((a, r) for a, r in self._requests if not r[0] == index)

    def delete_all_for_peer(self, peer_address: peer_state.PeerAddress):
        self._requests = set((a, r) for a, r in self._requests if not a == peer_address)

    def delete_all(self):
        self._requests = set()

    def existing_requests_for_peer(self, peer_address: peer_state.PeerAddress) -> Set[Tuple[int,int,int]]:
        return set(r for a, r in self._requests if a == peer_address)

