from enum import Enum
from typing import Any, Tuple, Optional

import bitarray
import trio

import torrent as tstate

from config import LISTENING_PORT, STREAM_CHUNK_SIZE

# peer listener and peer sender
# one listener for all peers
# potentially multiple senders?
# does this mean a single peer can send stuff over
# the incoming connection or a specific outbound
# connection that I opened?

class PeerType(Enum):
    SERVER = 0
    CLIENT = 1

class PeerMsg(Enum):
    CHOKE = b'0'
    UNCHOKE = b'1'
    INTERESTED = b'2'
    NOT_INTERESTED = b'3'
    HAVE = b'4'
    BITFIELD = b'5'
    REQUEST = b'6'
    PIECE = b'7'
    CANCEL = b'8'

def parse_have(s: bytes) -> int:
    return int(s)

def parse_bitfield(s: bytes) -> bitarray:
    # NOTE the input will be an integer number of bytes, so it may
    # have extra bits
    b = bitarray.bitarray()
    b.frombytes(s)
    return b

def parse_request_or_cancel(s: bytes) -> Tuple[int,int,int]:
    # This should be 12 bytes in most cases, so I'm hardcoding it for now.
    index = int.from_bytes(s[:4], byteorder='big')
    begin = int.from_bytes(s[4:8], byteorder='big')
    length = int.from_bytes(s[8:], byteorder='big')
    return (index, begin, length)

def parse_piece(s):
    index = from_bytes(s[:4], byteorder='big')
    begin = from_bytes(s[4:8], byteorder='big')
    data = s[8:]
    return (index, begin, data)


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

    async def receive_message(self) -> Tuple[int, bytes]:
        msg_length = None # self._msg_data persists between calls but msg_length resets each time
        while True:
            data = await self._stream.receive_some(STREAM_CHUNK_SIZE)
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
        await self._stream.sendall(data)

    async def send_keepalive(self) -> None:
        data = (0).to_bytes(4, byteorder='big')
        await self._stream.sendall(data)


class PeerEngine(object):
    '''
    PeerEngine is initialized with a stream and two queues.
    '''
    def __init__(self, stream, recieved_queue, to_send_queue):
        #self._torrent = torrent
        self._peer_stream = PeerStream(stream)
        self._received_queue = recieved_queue
        self._to_send_queue = to_send_queue
        #
        #peer_info = stream.socket.getpeername()
        #ip: string = peer_info[0]
        #port: int = peer_info[1]
        #peer = tstate.Peer(ip, port)
        #self._peer_state = torrent.get_or_add_peer(peer)

    async def run(self):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.receiving_loop)
            nursery.start_soon(self.sending_loop)

    async def receiving_loop(self):
        while True:
            (length, data) = await self._peer_stream.receive_message()
            #self._peer_stream.last_seen = datetime.datetime.now()
            if length == 0:
                # keepalive message
                pass
            else:
                msg_type = data[0]
                msg_payload = data[1:]
                if msg_type == PeerMsg.CHOKE:
                    pass
                elif msg_type == PeerMsg.UNCHOKE:
                    pass
                elif msg_type == PeerMsg.INTERESTED:
                    pass
                elif msg_type == PeerMsg.NOT_INTERESTED:
                    pass
                elif msg_type == PeerMsg.HAVE:
                    index: int = parse_have(msg_payload)
                    #self._peer_stream.pieces[index] = True
                elif msg_type == PeerMsg.BITFIELD:
                    bitfield = parse_bitfield(msg_payload)
                    #self._peer_state.set_pieces(bitfield)
                elif msg_type == PeerMsg.REQUEST:
                    reqest_info = parse_request_or_cancel(msg_payload)
                    #self._peer_state.add_request(request_info)
                elif msg_type == PeerMsg.PIECE:
                    (index, begin, data) = parse_piece(msg_payload)
                    #self._torrent.add_piece(index, begin, data)
                elif msg_type == PeerMsg.CANCEL:
                    request_info = parse_request_or_cancel(msg_payload)
                    #self._peer_state.cancel_request(request_info)
                else:
                    # TODO - Exceptions are bad here!
                    raise Exception('bad peer message')

    async def sending_loop(self):
        while True:
            to_send = await self._to_send_queue.get()


async def start_peer_engine(engine, peer_state, stream):
    '''
    Find (or create) queues for relevant stream, and create PeerEngine.
    '''
    peer_engine = PeerEngine(stream, engine.msg_from_peer, peer_state.to_send_queue)
    await peer_engine.run()


#async def torrent_handler(torrent, stream):
#    peer_info = stream.socket.getpeername()
#    ip: string = peer_info[0]
#    port: int = peer_info[1]
#    peer = tstate.Peer(ip, port)
#    peer_state = torrent.get_or_add_peer(peer)
#    #
#    #peer_stream = PeerStream(stream)
#    peer_engine = Peer

def make_handler(engine):
    async def handler(stream):
        peer_info = stream.socket.getpeername()
        ip: string = peer_info[0]
        port: int = peer_info[1]
        peer = tstate.Peer(ip, port)
        print('Received incoming peer connection from {}'.format(peer))
        peer_state = await engine.get_or_add_peer(peer, PeerType.SERVER)
        await start_peer_engine(engine, peer_state, stream)
    return handler

async def make_standalone(engine, peer, peer_state):
    print('Starting outgoing peer connection to {}'.format(peer))
    stream = await trio.open_tcp_stream(peer.ip, peer.port)
    await start_peer_engine(engine, peer_state, stream)
