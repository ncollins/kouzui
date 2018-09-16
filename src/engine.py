import datetime
import hashlib
import io
import logging
import math
import random 
from typing import List, Dict, Tuple, Set

import bitarray
import trio

import bencode
import display
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
    logger.debug('stats updated: {}'.format(stats))


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
        self._received_blocks: Dict[int, Tuple[bitarray, bytearray]] = dict()
        self.requests = requests.RequestManager()
        # create FileManager and check hashes if file already exists
        self.file_manager = file_manager.FileManager(self._state
                , self._complete_pieces_to_write
                , self._write_confirmations
                , self._blocks_to_read 
                , self._blocks_for_peers
                )
        existing_hashes = self.file_manager.create_file_or_return_hashes()
        if existing_hashes:
            for index, h in enumerate(existing_hashes):
                piece_info = self._state.piece_info(index)
                if piece_info.sha1hash == h:
                    self._state._complete[index] = True


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
            nursery.start_soon(self.choking_loop)
            nursery.start_soon(self.delete_stale_requests_loop, config.DELETE_STALE_REQUESTS_SECONDS)

    async def info_loop(self):
        while True:
            unwritten_blocks = len(self._received_blocks.items())
            outstanding_requests = self.requests.size
            logger.info('stats = {}'.format(stats))
            logger.info('{} unwritten blocks, {} outstanding_requests, {}/{} complete pieces'.format(unwritten_blocks, outstanding_requests, sum(self._state._complete), len(self._state._complete)))
            if sum(self._state._complete) - len(self._state._complete) - sum(self._state._complete) > 0.97:
                logger.info('Outstanding requests = {}'.format(self.requests._requests))
                unwritten_blocks = [(i,b, len(data))
                        for i, blocks in self._received_blocks.items()
                        for b, data in blocks
                        ]
                logger.info('Unwritten blocks: {}'.format(unwritten_blocks))
            queues = [
                    self._peers_without_connection
                    , self._complete_pieces_to_write
                    , self._write_confirmations
                    , self._blocks_to_read
                    , self._blocks_for_peers
                    , self._msg_from_peer
                    ]
            logger.info('Queues  {}'.format([q.statistics() for q in queues]))
            logger.info('Alive peers {}'.format(self._peers.keys()))
            display.print_peers(self._state, self._peers)
            await trio.sleep(1)

    async def tracker_loop(self):
        new = True
        while True:
            logger.debug('tracker_loop')
            start_time = trio.current_time()
            event = b'started' if new else None
            raw_tracker_info = await tracker.query(self._state, event)
            tracker_info = bencode.parse_value(io.BytesIO(raw_tracker_info))
            # update peers
            # TODO we could recieve peers in a different format
            peer_ips_and_ports = bencode.parse_peers(tracker_info[b'peers'], self._state)
            peers = [(peer_state.PeerAddress(ip, port), peer_id) for ip, port, peer_id in peer_ips_and_ports]
            logger.info('Found peers from tracker: {}'.format(peers))
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
        logger.debug('starting peer_clients_loop')
        async with trio.open_nursery() as nursery:
            while True:
                logger.debug('peer_clients_loop')
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
        if self._state._complete.all():
            logger.info('Not making new requests, download is complete')
            return
        if not self._peers:
            logger.info('Not making new requests as there are no peers')
            return
        for address, peer_state in self._peers.items():
            if peer_state.is_client_choked:
                continue
            # TODO don't read private field of another object
            targets = (~self._state._complete) & peer_state._pieces
            indexes = [i for i, b in enumerate(targets) if b]
            if indexes:
                target_index = random.choice(indexes)
                logger.info('{}: self any? {}, peer any? {}, target_index = {}'.format(address, self._state._complete.any(), peer_state._pieces.any(), target_index))
                existing_requests = self.requests.existing_requests_for_peer(address)
                if len(existing_requests) > config.MAX_OUTSTANDING_REQUESTS_PER_PEER:
                    logger.info('{}: Not making new requests: {} existing'.format(address, len(existing_requests)))
                    new_requests = set()
                else:
                    suggested_requests = self._blocks_from_index(target_index)
                    new_requests = suggested_requests.difference(existing_requests)
                    logger.info('{}: {} suggested requests, {} existing'.format(address, len(suggested_requests), len(existing_requests)))
                logger.info('{}: new_requests = {}'.format(address, new_requests))
                if new_requests:
                    for r in new_requests:
                        self.requests.add_request(address, r)
                        incStats('requests_out')
                    await peer_state.to_send_queue.put(("blocks_to_request", new_requests))
            else:
                logger.info('No target pieces for {}'.format(address))


    async def handle_peer_message(self, peer_id, msg_type, msg_payload):
        if peer_id not in self._peers:
            logger.info('did not handle message because peer {} no longer exists'.format(peer_id))
            return
        peer_state = self._peers[peer_id]
        if msg_type == messages.PeerMsg.CHOKE:
            logger.info('Received CHOKE from {}'.format(peer_id))
            peer_state.choke_us()
        elif msg_type == messages.PeerMsg.UNCHOKE:
            logger.info('Received UNCHOKE from {}'.format(peer_id))
            peer_state.unchoke_us()
        elif msg_type == messages.PeerMsg.INTERESTED:
            logger.warning('Received INTERESTED from {} (not implemented)'.format(peer_id)) # TODO
        elif msg_type == messages.PeerMsg.NOT_INTERESTED:
            logger.warning('Received NOT_INTERESTED from {} (not implemented)'.format(peer_id)) # TODO
        elif msg_type == messages.PeerMsg.HAVE:
            index: int = messages.parse_have(msg_payload)
            logger.debug('Received HAVE {} from {}'.format(index, peer_id))
            peer_state.get_pieces()[index] = True
        elif msg_type == messages.PeerMsg.BITFIELD:
            logger.info('Received BITFIELD from {}'.format(peer_id))
            # TODO would be useful to log what percentage of the file the peer has
            bitfield = messages.parse_bitfield(msg_payload)
            peer_state.set_pieces(bitfield)
        elif msg_type == messages.PeerMsg.REQUEST:
            incStats('requests_in')
            request_info: Tuple[int,int,int] = messages.parse_request_or_cancel(msg_payload)
            logger.info('Received REQUEST from {} from {}'.format(request_info, peer_state.peer_id))
            index = request_info[0]
            if peer_state.is_peer_choked:
                logger.warning('{} requested {} but peer is choked'.format(peer_state.peer_id, index))
            elif self._state._complete[index]:
                await self._blocks_to_read.put((peer_state.peer_id, request_info))
            else:
                logger.warning('{} requested {} but piece is incomplete'.format(peer_state.peer_id, index))
        elif msg_type == messages.PeerMsg.PIECE:
            (index, begin, data) = messages.parse_piece(msg_payload)
            incStats('blocks_in')
            logger.info('Received block {} from {}'.format((index, begin, len(data)), peer_state.peer_id))
            peer_state.inc_download_counters()
            await self.handle_block_received(index, begin, data)
        elif msg_type == messages.PeerMsg.CANCEL:
            logger.warning('Received CANCEL from {} (not implemented)'.format(peer_id)) # TODO
            request_info = messages.parse_request_or_cancel(msg_payload)
        else:
            # TODO - Exceptions are bad here! Should this be assert false?
            logger.warning('Bad message: length = {}'.format(length))
            logger.warning('Bad message: data = {}'.format(data))
            raise Exception('bad peer message')

    async def handle_block_received(self, index: int, begin: int, data: bytes) -> None:
        if index not in self._received_blocks:
            piece_length = self._state.piece_length(index)
            completed_blocks = bitarray.bitarray(math.ceil(piece_length/config.BLOCK_SIZE))
            completed_blocks.setall(False)
            piece_data = bytearray(piece_length)
            self._received_blocks[index] = (completed_blocks, piece_data)
        else:
            completed_blocks = self._received_blocks[index][0]
            piece_data = self._received_blocks[index][1]
        block_index = begin // config.BLOCK_SIZE
        completed_blocks[block_index] = True
        piece_data[begin:begin+len(data)] = data
        if completed_blocks.all():
            piece_info = self._state.piece_info(index)
            complete_piece = bytes(piece_data)
            if hashlib.sha1(complete_piece).digest() == piece_info.sha1hash:
                self._received_blocks.pop(index) # TODO is this ordering significant?
                await self._complete_pieces_to_write.put((index, complete_piece))
            else:
                self._received_blocks.pop(index)
                self.requests.delete_all_for_piece(index)
                logger.warning('sha1hash does not match for index {}'.format(index))

    async def peer_messages_loop(self):
        while True:
            logger.debug('peer_messages_loop')
            peer_state, msg_type, msg_payload = await self._msg_from_peer.get() # TODO should use peer_id
            logger.debug('Engine recieved peer message from {}'.format(peer_state.peer_id))
            await self.handle_peer_message(peer_state.peer_id, msg_type, msg_payload)# TODO should use peer_id
            await self.update_peer_requests()

    async def announce_have_piece(self, index):
        peers = self._peers.copy() # shallow copy, but that should be enough as we're not modifying the PeerState objects
        for _peer_id, peer_s in peers.items():
            await peer_s.to_send_queue.put(('announce_have_piece', index))

    async def file_write_confirmation_loop(self):
        while True:
            logger.debug('file_write_confirmation_loop')
            index = await self._write_confirmations.get()
            self.requests.delete_all_for_piece(index)
            # NB - update the _complete vector first to guarantee that new clients get
            # the most upto date bitfield (they may also get a redundant HAVE message)
            self._state._complete[index] = True # TODO remove private property access
            await self.announce_have_piece(index)
            await self.update_peer_requests()

    async def file_reading_loop(self):
        while True:
            logger.debug('file_reading_loop')
            peer_id, block_details, block = await self._blocks_for_peers.get()
            incStats('blocks_out')
            if peer_id in self._peers:
                p_state = self._peers[peer_id]
                p_state.inc_upload_counters()
                await p_state.to_send_queue.put(('block_to_upload', (block_details, block)))
            else:
                logger.info('dropped block {} for {} because peer no longer exists'.format(block_details, peer_id))

    async def choking_loop(self):
        period = 0
        optimistic_unchoke = None
        while True:
            await trio.sleep(10)
            peers =  [ (peer_id, peer_s.get_20_second_rolling_download_count())
                    for peer_id, peer_s in self._peers.items() ]
            if period == 0 and peers:
                optimistic_unchoke = random.choice(peers)[0]
            peers = sorted(peers, key=lambda x: x[1], reverse=True)
            logger.info('Peers ordered by successful downloads in last 20 seconds: {}'.format(peers))
            # First X are unchoked
            # Rest are choked
            unchoke = set(p[0] for p in peers[:config.NUM_UNCHOKED_PEERS])
            choke = set(p[0] for p in peers[config.NUM_UNCHOKED_PEERS:])
            if optimistic_unchoke:
                unchoke.add(optimistic_unchoke)
                choke.discard(optimistic_unchoke)
            for p_id in unchoke:
                if p_id in self._peers: # protect against state change while putting in queue
                    p_state = self._peers[p_id]
                    alert = p_state.unchoke_them()
                    p_state.reset_rolling_download_count()
                    if alert == peer_state.ChokeAlert.ALERT:
                        await p_state.to_send_queue.put(('unchoke',None))
            for p_id in choke:
                if p_id in self._peers: # protect against state change while putting in queue
                    p_state = self._peers[p_id]
                    alert = p_state.choke_them()
                    p_state.reset_rolling_download_count()
                    if alert == peer_state.ChokeAlert.ALERT:
                        await p_state.to_send_queue.put(('choke',None))
            # update period
            period = (period + 1) % 3 # rotate period every 30 seconds

    async def delete_stale_requests_loop(self,seconds):
        while True:
            await trio.sleep(seconds)
            count = self.requests.delete_older_than(seconds=seconds)
            logging.info('Deleted {} stale requests (older than {} seconds)'.format(count, seconds))


def run(torrent):
    try:
        engine = Engine(torrent)
        trio.run(engine.run)
    except KeyboardInterrupt:
        print()
        print('Shutting down without cleanup...')
