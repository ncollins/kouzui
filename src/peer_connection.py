import logging
from typing import Tuple

import bitarray
import trio

import messages
import peer_state

from config import STREAM_CHUNK_SIZE

logger = logging.getLogger('peer')

class PeerStream(object):
    '''
    The aim is to wrap a stream with a peer protocol
    handler in the same way that Http_stream wraps
    a stream. The only "logic" needed for recieving messages
    is to find the length first and then keep accumulating data
    until it has enough.
    '''
    def __init__(self, stream, keepalive_gap_in_seconds = 110):
        self._stream = stream
        self._msg_data = b''
        self._keepalive_gap_in_seconds = keepalive_gap_in_seconds
        # send keep-alives at least every 2 mins

    async def receive_handshake(self):
        logger.debug('Starting to received handshake on {}'.format(self._stream))
        while len(self._msg_data) < 68:
            data = await self._stream.receive_some(STREAM_CHUNK_SIZE)
            if data == b'':
                raise Exception('EOF in handshake')
            logger.debug('Initial incoming handshake data from {}: {}'.format(self._stream.socket.getpeername(), data))
            self._msg_data += data
        handshake_data = self._msg_data[:68]
        self._msg_data = self._msg_data[68:]
        logger.debug('Final incoming handshake data {}'.format(data))
        return handshake_data

    async def receive_message(self) -> Tuple[int, bytes]:
        msg_length = None # self._msg_data persists between calls but msg_length resets each time
        while True:
            data = await self._stream.receive_some(STREAM_CHUNK_SIZE)
            #logging.info('received_message')
            if data != b'':
                logger.debug('received_message: Got peer data, first 10 bytes: {}'.format(data[:10]))
            else:
                raise Exception('EOF')
            self._msg_data += data
            # 1) see if we have enough to get message length, if not continue
            if msg_length is None and len(self._msg_data) < 4:
                continue
            # 2) get message length if we don't yet have it
            if msg_length is None:
                msg_length = int.from_bytes(self._msg_data[:4], byteorder='big')
                self._msg_data = self._msg_data[4:]
            # 3) get data if possible
            if (msg_length is not None) and len(self._msg_data) >= msg_length:
                msg = self._msg_data[:msg_length]
                self._msg_data = self._msg_data[msg_length:]
                return (msg_length, msg)

    async def send_message(self, msg: bytes) -> None:
        l = len(msg)
        data = l.to_bytes(4, byteorder='big') + msg
        await self._stream.send_all(data)

    async def send_handshake(self, info_hash, peer_id):
        handshake_data =  b'\x13BitTorrent protocol' + (b'\0' * 8) + info_hash + peer_id
        logger.debug('Sending handshake')
        logger.debug('Outgoing handshake = {}'.format(handshake_data))
        logger.debug('Length of outgoing handshake {}'.format(len(handshake_data)))
        await self._stream.send_all(handshake_data)
        logger.debug('Sent handshake')

    async def send_keepalive(self) -> None:
        data = (0).to_bytes(4, byteorder='big')
        await self._stream.sendall(data)


class PeerEngine(object):
    '''
    PeerEngine is initialized with a stream and two queues.
    '''
    def __init__(self, tstate, peer_address, peer_state, stream, recieved_queue, to_send_queue):
        self._tstate = tstate
        self._peer_address = peer_address
        self._peer_state = peer_state
        self._peer_stream = PeerStream(stream)
        self._received_queue = recieved_queue
        self._to_send_queue = to_send_queue

    async def run(self, initiate=True):
        try:
            # Do handshakes before starting main loops
            if initiate == True:
                await self.send_handshake()
                await self.receive_handshake()
            else:
                await self.receive_handshake()
                await self.send_handshake()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(self.receiving_loop)
                nursery.start_soon(self.sending_loop)
        except Exception as e:
            logger.debug('Closing PeerEngine')
            raise e

    async def receive_handshake(self):
        # First, receive handshake
        data = await self._peer_stream.receive_handshake()
        logger.debug('Handshake data = {}'.format(data))
        # Second, validation
        if len(data) < 20 + 8 + 20 + 20:
            raise Exception('Handshake data: wrong length')
        header = data[:20]
        _reserved_bytes = data[20:20+8]
        sha1hash = data[20+8:20+8+20]
        peer_id = data[20+8+20:20+8+20+20]
        if not (header == b'\x13BitTorrent protocol'):
            raise Exception('Handshake data: wrong header')
        if not (sha1hash == self._tstate.info_hash):
            raise Exception('Handshake data: wrong hash')
        if self._peer_state.peer_id:
            if not self._peer_state.peer_id == peer_id:
                raise Exception('Handshake data: peer_id does not match')
        else:
            self._peer_state.set_peer_id(peer_id)
        logger.info('Received handshake from {}'.format(self._peer_address))

    async def send_handshake(self):
        # Handshake
        await self._peer_stream.send_handshake(self._tstate.info_hash, self._tstate.peer_id)
        logger.info('Sent handshake to {}'.format(self._peer_address))

    async def receiving_loop(self):
        while True:
            (length, data) = await self._peer_stream.receive_message()
            logger.debug('Received message of length {}'.format(length))
            if length == 0:
                # keepalive message
                pass
            else:
                msg_type = data[0]
                msg_payload = data[1:]
                logger.debug('Putting message in queue for engine')
                await self._received_queue.put((self._peer_state, msg_type, msg_payload))

    async def send_bitfield(self):
        raw_pieces = self._tstate._complete # TODO don't use private property
        raw_msg = bytes([messages.PeerMsg.BITFIELD])
        raw_msg += raw_pieces.tobytes()
        await self._peer_stream.send_message(raw_msg)

    async def sending_loop(self):
        logger.info('About to send bitfield to {}'.format(self._peer_address))
        await self.send_bitfield()
        logger.info('Sent bitfield to {}'.format(self._peer_address))
        while True:
            command, data = await self._to_send_queue.get()
            if command == 'blocks_to_request':
                for index, begin, length in data:
                    raw_msg = bytes([messages.PeerMsg.REQUEST])
                    raw_msg += (index).to_bytes(4, byteorder='big')
                    raw_msg += (begin).to_bytes(4, byteorder='big')
                    raw_msg += (length).to_bytes(4, byteorder='big')
                    await self._peer_stream.send_message(raw_msg)
            elif command == 'block_to_upload':
                (index, begin, length), block_data = data
                raw_msg = bytes([messages.PeerMsg.PIECE])
                raw_msg += (index).to_bytes(4, byteorder='big')
                raw_msg += (begin).to_bytes(4, byteorder='big')
                raw_msg += block_data
                logger.info('Uploading block {}'.format((index, begin, length)))
                await self._peer_stream.send_message(raw_msg)


async def start_peer_engine(engine, peer_address, peer_state, stream, initiate=True):
    '''
    Find (or create) queues for relevant stream, and create PeerEngine.
    '''
    peer_engine = PeerEngine(engine._state, peer_address, peer_state, stream, engine.msg_from_peer, peer_state.to_send_queue)
    await peer_engine.run(initiate=True)


def make_handler(engine):
    async def handler(stream):
        try:
            peer_info = stream.socket.getpeername()
            ip: string = peer_info[0]
            port: int = peer_info[1]
            peer_address = peer_state.PeerAddress(ip, port)
            logger.debug('Received incoming peer connection from {}'.format(peer_address))
            peer_state = await engine.get_or_add_peer(peer_address, peer_state.PeerType.SERVER)
            await start_peer_engine(engine, peer_address, peer_state, stream, initiate=False)
        except Exception as e: # TODO this might be too general
            logger.warning('Failed to maintain peer connection to {} because of {}'.format(peer_address, e))
            await engine.failed_peers.put(peer_address)
    return handler

async def make_standalone(engine, peer_address, peer_state):
    logger.debug('Starting outgoing peer connection to {}'.format(peer_address))
    try:
        stream = await trio.open_tcp_stream(peer_address.ip, peer_address.port)
        await start_peer_engine(engine, peer_address, peer_state, stream, initiate=True)
    except Exception as e: # TODO this might be too general
        logger.warning('Failed to maintain peer connection to {} because of {}'.format(peer_address, e))
        await engine.failed_peers.put(peer_address)
