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
import messages
import peer_connection
import requests
import peer_state
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


class Engine(object):
    def __init__(self, torrent: state.Torrent) -> None:
        self._state = torrent
        # interact with self
        self._peers_without_connection = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        # interact with FileManager
        self._complete_pieces_to_write = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        self._write_confirmations      = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        self._blocks_to_read           = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        self._blocks_for_peers         = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        # interact with peer connections 
        self._msg_from_peer            = trio.Queue(config.INTERNAL_QUEUE_SIZE)
        # queues for sending TO peers are initialized on a per-peer basis
        self._peers: Dict[bytes, peer_state.PeerState] = dict()
        # data received but not written to disk
        self._received_blocks: Dict[int, Set[Tuple[int,bytes]]] = dict()
        self.requests = requests.RequestManager()
        self.file_manager = file_manager.FileManager(self._state
                , self._complete_pieces_to_write
                , self._write_confirmations
                , self._blocks_to_read 
                , self._blocks_for_peers
                )

    @property
    def msg_from_peer(self) -> trio.Queue:
        return self._msg_from_peer

    async def run(self):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.peer_clients_loop)
            nursery.start_soon(self.peer_server_loop)
            nursery.start_soon(self.tracker_loop)
            nursery.start_soon(self.peer_messages_loop)
            nursery.start_soon(self.file_write_confirmation_loop)
            nursery.start_soon(self.file_manager.run)
            nursery.start_soon(self.file_reading_loop)
            nursery.start_soon(self.info_loop)

    async def info_loop(self):
        while True:
            await trio.sleep(4)
            logging.info('info_loop --------------')
            unwritten_blocks = len(self._received_blocks.items())
            outstanding_requests = self.requests.size
            logging.info('stats = {}'.format(stats))
            logging.info('{} unwritten blocks, {} outstanding_requests, {}/{} complete pieces'.format(unwritten_blocks, outstanding_requests, sum(self._state._complete), len(self._state._complete)))
            if len(self._state._complete) - sum(self._state._complete) < 2:
                logging.info('Outstanding requests = {}'.format(self.requests._requests))
            queues = [
                    self._peers_without_connection
                    , self._complete_pieces_to_write
                    , self._write_confirmations
                    , self._blocks_to_read
                    , self._blocks_for_peers
                    , self._msg_from_peer
                    ]
            logging.info('Queue lengths {}'.format([q.statistics() for q in queues]))
            logging.info('Alive peers {}'.format(self._peers.keys()))

    async def tracker_loop(self):
        new = True
        while True:
            logging.info('tracker_loop')
            start_time = trio.current_time()
            event = b'started' if new else None
            raw_tracker_info = await tracker.query(self._state, event)
            tracker_info = bencode.parse_value(io.BytesIO(raw_tracker_info))
            # update peers
            # TODO we could recieve peers in a different format
            peer_ips_and_ports = bencode.parse_peers(tracker_info[b'peers'], self._state)
            peers = [(peer_state.PeerAddress(ip, port), peer_id) for ip, port, peer_id in peer_ips_and_ports]
            logger.debug('Found peers from tracker: {}'.format(peers))
            await self.update_peers(peers)
            # update other info: 
            #self._state.complete_peers = tracker_info['complete']
            #self._state.incomplete_peers = tracker_info['incomplete']
            #self._state.interval = int(tracker_info['interval'])
            # tell tracker the new interval
            await trio.sleep_until(start_time + self._state.interval)
            new = False

    async def peer_server_loop(self):
        await trio.serve_tcp(peer_connection.make_handler(self), self._state.listening_port)

    async def peer_clients_loop(self):
        '''
        Start up clients for new peers that are not from the serve.
        '''
        logger.debug('Peer client loop!!!')
        async with trio.open_nursery() as nursery:
            while True:
                logging.info('peer_clients_loop')
                address = await self._peers_without_connection.get()
                nursery.start_soon(peer_connection.make_standalone, self, address)

    async def update_peers(self, peers: List[peer_state.PeerAddress]) -> None:
        for address, peer_id in peers:
            if peer_id in self._peers:
                logger.info('Peer already exists: {}'.format(peer_id))
            else:
                logger.info('Adding new peer to queue: {} / {}'.format(address, peer_id))
                await self._peers_without_connection.put(address)

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
            if peer_state.is_choked:
                continue
            logger.info('`update_peer_requests` peer_id = {}'.format(address))
            # TODO don't read private field of another object
            targets = (~self._state._complete) & peer_state._pieces
            indexes = [i for i, b in enumerate(targets) if b]
            random.shuffle(indexes)
            logger.info('`update_peer_requests` indexes[:5] = {}, self any? {}, peer any? {}'.format(indexes, self._state._complete.any(), peer_state._pieces.any()))
            if indexes:
                existing_requests = self.requests.existing_requests_for_peer(address)
                if len(existing_requests) > 52: #config.MAX_OUTSTANDING_REQUESTS_PER_PEER:
                    logger.info('Not making new requests: # existing for peer <{}> = {}'.format(address, len(existing_requests)))
                    new_requests = set()
                else:
                    suggested_requests = self._blocks_from_index(indexes[0])
                    new_requests = suggested_requests.difference(existing_requests)
                    logger.info('# suggested requests = {}, # existing for peer <{}> = {}'.format(len(suggested_requests), address, len(existing_requests)))
                logger.info('Blocks to request: {}'.format(new_requests))
                if new_requests:
                    for r in new_requests:
                        self.requests.add_request(address, r)
                        incStats('requests_out')
                    await peer_state.to_send_queue.put(("blocks_to_request", new_requests))

    async def handle_peer_message(self, peer_id, msg_type, msg_payload):
        if peer_id not in self._peers:
            logging.info('did not handle message because peer {} no longer exists'.format(peer_id))
            return
        peer_state = self._peers[peer_id]
        if msg_type == messages.PeerMsg.CHOKE:
            logger.info('Received CHOKE from {}'.format(peer_id))
            peer_state.choke()
        elif msg_type == messages.PeerMsg.UNCHOKE:
            logger.info('Received UNCHOKE from {}'.format(peer_id))
            peer_state.unchoke()
        elif msg_type == messages.PeerMsg.INTERESTED:
            logger.warning('Received INTERESTED from {} (not implemented)'.format(peer_id)) # TODO
        elif msg_type == messages.PeerMsg.NOT_INTERESTED:
            logger.warning('Received NOT_INTERESTED from {} (not implemented)'.format(peer_id)) # TODO
        elif msg_type == messages.PeerMsg.HAVE:
            logger.debug('Received HAVE from {}'.format(peer_id))
            index: int = messages.parse_have(msg_payload)
            peer_state.get_pieces()[index] = True
        elif msg_type == messages.PeerMsg.BITFIELD:
            logger.info('Received BITFIELD from {}'.format(peer_id))
            bitfield = messages.parse_bitfield(msg_payload)
            peer_state.set_pieces(bitfield)
            await self.update_peer_requests()
        elif msg_type == messages.PeerMsg.REQUEST:
            incStats('requests_in')
            request_info: Tuple[int,int,int] = messages.parse_request_or_cancel(msg_payload)
            logger.info('Received REQUEST from {} from {}'.format(request_info, peer_state.peer_id))
            await self._blocks_to_read.put((peer_state.peer_id, request_info))
        elif msg_type == messages.PeerMsg.PIECE:
            (index, begin, data) = messages.parse_piece(msg_payload)
            incStats('blocks_in')
            logger.info('Received block {} from {}'.format((index, begin, len(data)), peer_state.peer_id))
            await self.handle_block_received(index, begin, data)
        elif msg_type == messages.PeerMsg.CANCEL:
            logger.warning('Received CANCEL from {} (not implemented)'.format(peer_id)) # TODO
            request_info = messages.parse_request_or_cancel(msg_payload)
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
            logging.info('Received sha1 {}: {}'.format((index,offset), hashlib.sha1(block_data).digest()))
            if offset == len(piece_data):
                piece_data = piece_data + block_data
            else:
                break
        piece_info = self._state.piece_info(index)
        if len(piece_data) == self._state.piece_length(index):
            if hashlib.sha1(piece_data).digest() == piece_info.sha1hash:
                self._received_blocks.pop(index) # TODO is this ordering significant?
                await self._complete_pieces_to_write.put((index, piece_data))
            else:
                self._received_blocks.pop(index)
                self.requests.delete_all_for_piece(index)
                logger.warning('sha1hash does not match for index {}'.format(index))
                await self.update_peer_requests()

    async def peer_messages_loop(self):
        while True:
            logging.info('peer_messages_loop')
            peer_state, msg_type, msg_payload = await self._msg_from_peer.get() 
            logger.debug('Engine: recieved peer message')
            await self.handle_peer_message(peer_state.peer_id, msg_type, msg_payload)
            await self.update_peer_requests()

    async def file_write_confirmation_loop(self):
        while True:
            logging.info('file_write_confirmation_loop')
            index = await self._write_confirmations.get()
            self.requests.delete_all_for_piece(index)
            self._state._complete[index] = True # TODO remove private property access
            await self.update_peer_requests()
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
            logging.info('file_reading_loop')
            peer_id, block_details, block = await self._blocks_for_peers.get()
            incStats('blocks_out')
            if peer_id in self._peers:
                p_state = self._peers[peer_id]
                await p_state.to_send_queue.put(('block_to_upload', (block_details, block)))
            else:
                logging.info('dropped block {} for {} because peer no longer exists'.format(block_details, peer_id))


def run(torrent):
    try:
        engine = Engine(torrent)
        trio.run(engine.run)
    except KeyboardInterrupt:
        print()
        print('Shutting down without cleanup...')
