import datetime
import hashlib
import io
import logging
import random 
from typing import List, Dict, Tuple, Set

import bitarray
import trio

import bencode
import file_manager
import peer
import torrent as state
import tracker

import config

logger = logging.getLogger('engine')

stats = { 'requests_in': 0
        , 'blocks_out': 0
        , 'requests_out': 0
        , 'blocks_in': 0
        }

def incStats(field):
    stats[field] += 1
    logger.info('STATS {}'.format(stats))

class RequestManager(object):
    '''
    Keeps track of blocks client requested by index, peer_address
    and block.
    '''
    def __init__(self):
        self._requests: Set[Tuple[state.PeerAddress,Tuple[int,int,int]]] = set()

    @property
    def size(self):
        return len(self._requests)

    def add_request(self, peer_address: state.PeerAddress, block: Tuple[int,int,int]):
        self._requests.add((peer_address, block))

    def delete_all_for_piece(self, index: int):
        to_delete = set((a, r) for a, r in self._requests if r[0] == index)
        logger.info('Found {} block requests to delete for piece index {}'.format(len(to_delete),index))
        self._requests = set((a, r) for a, r in self._requests if not r[0] == index)

    def delete_all_for_peer(self, peer_address: state.PeerAddress):
        self._requests = set((a, r) for a, r in self._requests if not a == peer_address)

    def delete_all(self):
        self._requests = set()

    #def number_outstanding_for_peer(self, peer_address: state.PeerAddress):
    #    return len([r for a, r in self._requests if a == peer_address])

    def existing_requests_for_peer(self, peer_address: state.PeerAddress) -> Set[Tuple[int,int,int]]:
        return set(r for a, r in self._requests if a == peer_address)


class Engine(object):
    def __init__(self, torrent: state.Torrent) -> None:
        self._state = torrent
        # interact with self
        self._peers_without_connection = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        self._failed_peers             = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        # interact with FileManager
        self._complete_pieces_to_write = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        self._write_confirmations      = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        self._blocks_to_read           = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        self._blocks_for_peers         = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        # interact with peer connections 
        self._msg_from_peer            = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        # queues for sending TO peers are initialized on a per-peer basis
        #self._queues_for_peers: Dict[state.Peer,trio.Queue] = dict()
        self._peers: Dict[state.PeerAddress, state.PeerState] = dict()
        # data received but not written to disk
        self._received_blocks: Dict[int, List[Tuple[int,bytes]]] = {}
        self.requests = RequestManager()
        self.file_manager = file_manager.FileManager(self._state
                , self._complete_pieces_to_write
                , self._write_confirmations
                , self._blocks_to_read 
                , self._blocks_for_peers
                )

    @property
    def msg_from_peer(self) -> trio.Queue:
        return self._msg_from_peer

    @property
    def failed_peers(self) -> trio.Queue:
        return self._failed_peers

    async def run(self):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.peer_clients_loop)
            nursery.start_soon(self.peer_server_loop)
            nursery.start_soon(self.tracker_loop)
            nursery.start_soon(self.peer_messages_loop)
            nursery.start_soon(self.file_write_confirmation_loop)
            nursery.start_soon(self.file_manager.run)
            nursery.start_soon(self.file_reading_loop)
            nursery.start_soon(self.remove_failed_peers_loop)

    async def tracker_loop(self):
        new = True
        while True:
            start_time = trio.current_time()
            event = b'started' if new else None
            raw_tracker_info = await tracker.query(self._state, event)
            tracker_info = bencode.parse_value(io.BytesIO(raw_tracker_info))
            # update peers
            # TODO we could recieve peers in a different format
            peer_ips_and_ports = bencode.parse_peers(tracker_info[b'peers'], self._state)
            peers = [state.PeerAddress(ip, port) for ip, port, _ in peer_ips_and_ports]
            logger.debug('Found peers: {}'.format(peers))
            await self.update_peers(peers)
            # update other info: 
            #self._state.complete_peers = tracker_info['complete']
            #self._state.incomplete_peers = tracker_info['incomplete']
            #self._state.interval = int(tracker_info['interval'])
            # tell tracker the new interval
            await trio.sleep_until(start_time + self._state.interval)
            new = False

    async def peer_server_loop(self):
        await trio.serve_tcp(peer.make_handler(self), self._state.listening_port)

    async def peer_clients_loop(self):
        '''
        Start up clients for new peers that are not from the serve.
        '''
        logger.debug('Peer client loop!!!')
        async with trio.open_nursery() as nursery:
            while True:
                (peer_id, peer_state) = await self._peers_without_connection.get()
                nursery.start_soon(peer.make_standalone, self, peer_id, peer_state)

    async def update_peers(self, peers: List[state.PeerAddress]) -> None:
        for p in peers:
            await self.get_or_add_peer(p, peer.PeerType.CLIENT)

    async def get_or_add_peer(self, address: state.PeerAddress, peer_type=peer.PeerType, peer_id=None) -> state.PeerState:
        # 1. get or create PeerState
        if address in self._peers:
            return self._peers[address]
        else:
            now = datetime.datetime.now()
            pieces = bitarray.bitarray(self._state._num_pieces)
            pieces.setall(False)
            peer_state = state.PeerState(pieces, now, peer_id=peer_id)
            self._peers[address] = peer_state
        # 2. start connection if needed
        # If server, then connection already exists
        if peer_type == peer.PeerType.SERVER:
            return peer_state
        elif peer_type == peer.PeerType.CLIENT:
            await self._peers_without_connection.put((address, peer_state))
            return peer_state
        else:
            assert(False)

    def _blocks_from_index(self, index):
        piece_length = self._state.piece_length(index)
        block_length = min(piece_length, config.BLOCK_SIZE)
        begin_indexes = list(range(0, piece_length, block_length))
        return set((index, begin, min(block_length, piece_length-begin))
                for begin in begin_indexes)

    async def update_peer_requests(self):
        # Look at what the client has, what the peers have
        # and update the requested pieces for each peer.
        logger.info('`update_peer_requests`')
        if self._state._complete.all():
            logger.info('Not making new requests, download is complete')
            return
        for address, peer_state in self._peers.items():
            logger.info('`update_peer_requests` address = {}'.format(address))
            # TODO don't read private field of another object
            targets = (~self._state._complete) & peer_state._pieces
            indexes = [i for i, b in enumerate(targets) if b]
            random.shuffle(indexes)
            logger.info('`update_peer_requests` indexes[:5] = {}, self any? {}, peer any? {}'.format(indexes, self._state._complete.any(), peer_state._pieces.any()))
            if indexes:
                existing_requests = self.requests.existing_requests_for_peer(address)
                if len(existing_requests) > config.MAX_OUTSTANDING_REQUESTS_PER_PEER:
                    logger.info('Not making new requests: # existing for peer <{}> = {}'.format(address, len(existing_requests)))
                    new_requests = set()
                else:
                    suggested_requests = self._blocks_from_index(indexes[0])
                    new_requests = suggested_requests.difference(existing_requests)
                    logger.info('# suggested requests = {}, # existing for peer <{}> = {}'.format(len(suggested_requests), address, len(existing_requests)))
                logger.info('Blocks to request: {}'.format(new_requests))
                for r in new_requests:
                    self.requests.add_request(address, r)
                incStats('requests_out')
                await peer_state.to_send_queue.put(("blocks_to_request", new_requests))

    async def handle_peer_message(self, peer_state, msg_type, msg_payload):
        if msg_type == peer.PeerMsg.CHOKE:
            logger.debug('Got CHOKE') # TODO
        elif msg_type == peer.PeerMsg.UNCHOKE:
            logger.debug('Got UNCHOKE') # TODO
        elif msg_type == peer.PeerMsg.INTERESTED:
            logger.debug('Got INTERESTED') # TODO
        elif msg_type == peer.PeerMsg.NOT_INTERESTED:
            logger.debug('Got NOT_INTERESTED') # TODO
        elif msg_type == peer.PeerMsg.HAVE:
            logger.debug('Got HAVE')
            index: int = peer.parse_have(msg_payload)
            peer_state.get_pieces()[index] = True
        elif msg_type == peer.PeerMsg.BITFIELD:
            logger.debug('Got BITFIELD')
            bitfield = peer.parse_bitfield(msg_payload)
            peer_state.set_pieces(bitfield)
        elif msg_type == peer.PeerMsg.REQUEST:
            logger.info('Got REQUEST') # TODO
            incStats('requests_in')
            request_info: Tuple[int,int,int] = peer.parse_request_or_cancel(msg_payload)
            await self._blocks_to_read.put((peer_state, request_info))
        elif msg_type == peer.PeerMsg.PIECE:
            (index, begin, data) = peer.parse_piece(msg_payload)
            incStats('blocks_in')
            logger.info('Got PIECE: {}'.format((index, begin, len(data))))
            await self.handle_block_received(index, begin, data)
            #self._torrent.add_piece(index, begin, data)
        elif msg_type == peer.PeerMsg.CANCEL:
            logger.debug('Got CANCEL') # TODO
            request_info = peer.parse_request_or_cancel(msg_payload)
            #self._peer_state.cancel_request(request_info)
        else:
            # TODO - Exceptions are bad here! Should this be assert false?
            logger.debug('Bad message: length = {}'.format(length))
            logger.debug('Bad message: data = {}'.format(data))
            raise Exception('bad peer message')

    async def handle_block_received(self, index: int, begin: int, data: bytes) -> None:
        if index not in self._received_blocks:
            self._received_blocks[index] = set()
        blocks_set = self._received_blocks[index]
        blocks_set.add((begin, data))
        blocks = sorted(blocks_set)
        piece_data = b''
        for offset, block_data in blocks:
            if offset == len(piece_data):
                piece_data = piece_data + block_data
            else:
                break
        piece_info = self._state.piece_info(index)
        if len(piece_data) == self._state.piece_length(index):
            if hashlib.sha1(piece_data).digest() == piece_info.sha1hash:
                await self._complete_pieces_to_write.put((index, piece_data))
                self._received_blocks.pop(index)
            else:
                self._received_blocks.pop(index)
                self.requests.delete_all_for_piece(index)
                logger.warning('sha1hash does not match for index {}'.format(index))
                await self.update_peer_requests()

    async def peer_messages_loop(self):
        while True:
            peer_state, msg_type, msg_payload = await self._msg_from_peer.get() 
            #
            logger.debug('Engine: recieved peer message')
            await self.handle_peer_message(peer_state, msg_type, msg_payload)
            await self.update_peer_requests()

    async def file_write_confirmation_loop(self):
        while True:
            index = await self._write_confirmations.get()
            self.requests.delete_all_for_piece(index)
            self._state._complete[index] = True # TODO remove private property access
            # TODO - send "HAVE" message
            logger.info('Have {} pieces of {}, with {} blocks outstanding'.format(sum(self._state._complete), len(self._state._complete), self.requests.size))
            if self._state._num_pieces - 3 < sum(self._state._complete) < self._state._num_pieces:
                print('Final blocks missing: {}'.format(self.requests._requests))
                unwritten_blocks = [(i,b, len(data))
                        for i, blocks in self._received_blocks.items()
                        for b, data in blocks
                        ]
                print('Unwritten blocks: {}'.format(unwritten_blocks))

    async def file_reading_loop(self):
        while True:
            peer_state, block_details, block = await self._blocks_for_peers.get()
            incStats('blocks_out')
            await peer_state.to_send_queue.put(('block_to_upload', (block_details, block)))

    async def remove_failed_peers_loop(self):
        while True:
            address = await self._failed_peers.get()
            logger.info('Removing peer: {}'.format(address))
            if address in self._peers:
                del self._peers[address]



def run(torrent):
    try:
        engine = Engine(torrent)
        trio.run(engine.run)
    except KeyboardInterrupt:
        print()
        print('Shutting down without cleanup...')
