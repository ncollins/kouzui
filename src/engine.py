import datetime
import io
from typing import List, Dict, Tuple

import bitarray
import trio

import bencode
import peer
import torrent as state
import tracker


class Engine(object):
    def __init__(self, torrent: state.Torrent) -> None:
        self._state = torrent
        # interact with self
        self._peers_without_connection = trio.Queue(100)
        # interact with FileManager
        self._complete_pieces_to_write = trio.Queue(100) # TODO remove magic number
        self._write_confirmation       = trio.Queue(100) # TODO remove magic number
        # interact with peer connections 
        self._msg_from_peer            = trio.Queue(100) # TODO remove magic number
        # queues for sending TO peers are initialized on a per-peer basis
        #self._queues_for_peers: Dict[state.Peer,trio.Queue] = dict()
        self._peers: Dict[state.Peer, state.PeerState] = dict()

    @property
    def msg_from_peer(self) -> trio.Queue:
        return self._msg_from_peer

    async def run(self):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.peer_clients)
            nursery.start_soon(self.peer_server)
            nursery.start_soon(self.tracker_loop)

    async def tracker_loop(self):
        new = True
        while True:
            start_time = trio.current_time()
            event = b'started' if new else None
            raw_tracker_info = await tracker.query(self._state, event)
            tracker_info = bencode.parse_value(io.BytesIO(raw_tracker_info))
            # update peers
            # TODO we could recieve peers in a different format
            peer_ips_and_ports = bencode.parse_peers(tracker_info[b'peers']) 
            peers = [state.Peer(ip, port) for ip, port in peer_ips_and_ports]
            print('Found peers: {}'.format(peers))
            await self.update_peers(peers)
            # update other info: 
            #self._state.complete_peers = tracker_info['complete']
            #self._state.incomplete_peers = tracker_info['incomplete']
            #self._state.interval = int(tracker_info['interval'])
            # tell tracker the new interval
            await trio.sleep_until(start_time + self._state.interval)
            new = False

    async def peer_server(self):
        await trio.serve_tcp(peer.make_handler(self), self._state.listening_port)

    async def peer_clients(self):
        '''
        Start up clients for new peers that are not from the serve.
        '''
        print('Peer client loop!!!')
        async with trio.open_nursery() as nursery:
            while True:
                (peer_id, peer_state) = await self._peers_without_connection.get()
                nursery.start_soon(peer.make_standalone, self, peer_id, peer_state)

    async def update_peers(self, peers: List[state.Peer]) -> None:
        for p in peers:
            await self.get_or_add_peer(p, peer.PeerType.CLIENT)

    async def get_or_add_peer(self, peer_id: state.Peer, peer_type=peer.PeerType) -> state.PeerState:
        # 1. get or create PeerState
        if peer_id in self._peers:
            return self._peers[peer_id]
        else:
            now = datetime.datetime.now()
            pieces = bitarray.bitarray(self._state._num_pieces)
            pieces.setall(False)
            peer_state = state.PeerState(pieces, now)
            self._peers[peer_id] = peer_state
        # 2. start connection if needed
        # If server, then connection already exists
        if peer_type == peer.PeerType.SERVER:
            return peer_state
        elif peer_type == peer.PeerType.CLIENT:
            await self._peers_without_connection.put((peer_id, peer_state))
            return peer_state
        else:
            assert(False)


#async def main_loop(torrent):
#    raw_tracker_info = await tracker.query(torrent)
#    tracker_info = bencode.parse_value(io.BytesIO(raw_tracker_info))
#    # TODO we could recieve peers in a different format
#    peers = bencode.parse_compact_peers(tracker_info[b'peers']) 
#    print(tracker_info)
#    print(peers)
#    for ip, port in peers:
#        peer = tstate.Peer(ip, port)
#        torrent.add_peer(peer)

def run(torrent):
    engine = Engine(torrent)
    trio.run(engine.run)
