from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Optional, TYPE_CHECKING

import trio

if TYPE_CHECKING:
    import engine
    import token_bucket
    import torrent
import peer_messages
import peer_state
from internal_messages import BlockForPeer
from utility_types import Block

from config import STREAM_CHUNK_SIZE, KEEPALIVE_SECONDS

logger = logging.getLogger("peer")


class PeerStream(object):
    """
    The aim is to wrap a stream with a peer protocol
    handler in the same way that HttpStream wraps
    a stream. The only "logic" needed for recieving messages
    is to find the length first and then keep accumulating data
    until it has enough.
    """

    def __init__(
        self, stream: trio.SocketStream, token_bucket: token_bucket.TokenBucket | None = None
    ):
        self._stream: trio.SocketStream = stream
        self._msg_data: bytes = b""
        self._token_bucket = token_bucket

    async def receive_handshake(self) -> bytes:
        logger.debug("Starting to received handshake on {}".format(self._stream))
        data = None
        while len(self._msg_data) < 68:
            data = await self._stream.receive_some(STREAM_CHUNK_SIZE)
            if data == b"":
                logger.debug(
                    "empty data in handshake, about to raise EOF from {}".format(self._stream)
                )
                raise Exception("EOF in handshake")
            logger.debug(
                "Initial incoming handshake data from {}: {!r}".format(
                    self._stream.socket.getpeername(), data
                )
            )
            self._msg_data += data
        handshake_data = self._msg_data[:68]
        self._msg_data = self._msg_data[68:]
        logger.debug("Final incoming handshake data {!r}".format(data))
        return handshake_data

    def _parse_msg_data(self) -> list[tuple[int, bytes]]:
        messages: list[tuple[int, bytes]] = []
        msg_length = None
        while True:
            total_length = len(self._msg_data)
            if total_length < 4:
                return messages
            else:
                msg_length = int.from_bytes(self._msg_data[:4], byteorder="big")
                if total_length < 4 + msg_length:
                    return messages
                else:
                    messages.append((msg_length, self._msg_data[4 : 4 + msg_length]))
                    self._msg_data = self._msg_data[4 + msg_length :]
                    logger.debug(
                        "Parsed message of length {} from {}".format(msg_length, self._stream)
                    )

    async def receive_message(self) -> list[tuple[int, bytes]]:
        logger.debug("Called receive_message for {}".format(self._stream))
        while True:
            messages = self._parse_msg_data()
            if messages:
                return messages
            else:
                data = await self._stream.receive_some(STREAM_CHUNK_SIZE)
                if data != b"":
                    logger.debug("received_message: Got {} from {}".format(len(data), self._stream))
                else:
                    logger.debug("empty data, about to raise EOF from {}".format(self._stream))
                    raise Exception("EOF")
                self._msg_data += data

    async def send_message(self, msg: bytes) -> None:
        message_length = len(msg)
        data = message_length.to_bytes(4, byteorder="big") + msg
        logger.debug("Pre-send message of length {} on {}".format(message_length, self._stream))
        if self._token_bucket is not None:
            while not self._token_bucket.check_and_decrement(len(data)):
                logger.debug("Token bucket is empty waiting 0.1s")
                await trio.sleep(self._token_bucket.update_period)
        await self._stream.send_all(data)
        logger.debug("Sent message of length {} on {}".format(message_length, self._stream))

    async def send_handshake(self, info_hash: bytes, peer_id: bytes) -> None:
        handshake_data = b"\x13BitTorrent protocol" + (b"\0" * 8) + info_hash + peer_id
        logger.debug("Sending handshake")
        logger.debug("Outgoing handshake = {!r}".format(handshake_data))
        logger.debug("Length of outgoing handshake {}".format(len(handshake_data)))
        await self._stream.send_all(handshake_data)
        logger.debug("Sent handshake")

    async def send_keepalive(self) -> None:
        data = (0).to_bytes(4, byteorder="big")
        await self._stream.send_all(data)


class HandshakeError(Exception):
    def __init__(self, reason: str, data: bytes) -> None:
        self.reason = reason
        self.data = data


class PeerEngine(object):
    """
    PeerEngine is initialized with a stream and two queues.
    """

    def __init__(
        self,
        eng: engine.Engine,
        peer_address: peer_state.PeerAddress,
        expected_peer_id: bytes | None,
        stream: trio.SocketStream,
        *,
        send_peer_msg_to_engine: trio.MemorySendChannel[tuple[peer_state.PeerState, int, bytes]],
    ):
        self._tstate: torrent.Torrent = eng._state
        self._eng: engine.Engine = eng
        self._peer_address: peer_state.PeerAddress = peer_address
        self._expected_peer_id: bytes | None = expected_peer_id
        self._peer_id_and_state: Optional[tuple[bytes, peer_state.PeerState]] = None
        self._peer_stream: PeerStream = PeerStream(stream, eng.token_bucket)
        self._send_peer_msg_to_engine: trio.MemorySendChannel[
            tuple[peer_state.PeerState, int, bytes]
        ] = send_peer_msg_to_engine
        self._receive_outgoing_data: Optional[trio.MemoryReceiveChannel[tuple[str, Any]]] = None

    async def run(self, initiate: bool = True) -> None:
        peer_id = None
        try:
            # Do handshakes before starting main loops
            if initiate:
                await self.send_handshake()
                peer_id = await self.receive_handshake()
            else:
                peer_id = await self.receive_handshake()
                await self.send_handshake()
            if peer_id in self._eng._peers:
                # We already have peer, close connection
                raise Exception("peer already exists")
            else:
                peer_s = peer_state.PeerState(
                    peer_id, self._tstate._num_pieces
                )  # TODO don't use private property
                self._eng._peers[peer_id] = peer_s
                self._peer_id_and_state = (peer_id, peer_s)
                self._receive_outgoing_data = peer_s.receive_outgoing_data
            async with trio.open_nursery() as nursery:
                nursery.start_soon(self.receiving_loop)
                nursery.start_soon(self.sending_loop)
        except Exception as e:
            if self._peer_id_and_state and peer_id is not None:
                self._eng._peers.pop(peer_id)
            logger.exception("Exception raised in PeerEngine")
            logger.info(
                "Closing PeerEngine {} / {}".format(self._peer_address, self._peer_id_and_state)
            )
            raise e
        except trio.MultiError:
            if self._peer_id_and_state and peer_id is not None:
                self._eng._peers.pop(peer_id)
            logger.exception("MultiError raised in PeerEngine")
            logger.info(
                "Closing PeerEngine {} / {}".format(self._peer_address, self._peer_id_and_state)
            )
            raise Exception("trio.MultiError was raised by PeerEngine")

    async def receive_handshake(self) -> bytes:
        # First, receive handshake
        data = await self._peer_stream.receive_handshake()
        logger.debug("Handshake data = {!r}".format(data))
        # Second, validation
        if len(data) < 20 + 8 + 20 + 20:
            raise HandshakeError("Handshake data: wrong length", data)
        header = data[:20]
        _reserved_bytes = data[20 : 20 + 8]
        sha1hash = data[20 + 8 : 20 + 8 + 20]
        peer_id = data[20 + 8 + 20 : 20 + 8 + 20 + 20]
        if not (header == b"\x13BitTorrent protocol"):
            raise HandshakeError("Handshake data: wrong header", header)
        if not (sha1hash == self._tstate.info_hash):
            raise HandshakeError("Handshake data: wrong hash", sha1hash)
        if self._expected_peer_id:
            if not self._expected_peer_id == peer_id:
                raise HandshakeError("Handshake data: peer_id does not match", peer_id)
        logger.debug("Received handshake from {}/{!r}".format(self._peer_address, peer_id))
        return peer_id

    async def send_handshake(self) -> None:
        # Handshake
        await self._peer_stream.send_handshake(self._tstate.info_hash, self._tstate.peer_id)
        logger.debug("Sent handshake to {}".format(self._peer_address))

    async def receiving_loop(self) -> None:
        assert self._peer_id_and_state is not None
        peer_id = self._peer_id_and_state[0]
        while True:
            logging.debug("receiving_loop for {!r}".format(peer_id))
            messages = await self._peer_stream.receive_message()
            for length, data in messages:
                logger.debug("Received message of length {} from {!r}".format(length, peer_id))
                if length == 0:
                    # keepalive message
                    pass
                else:
                    msg_type = data[0]
                    msg_payload = data[1:]
                    logger.debug("Putting message in queue for engine")
                    await self._send_peer_msg_to_engine.send(
                        (self._peer_id_and_state[1], msg_type, msg_payload)
                    )  # TODO should use peer_id

    async def send_bitfield(self) -> None:
        raw_pieces = self._tstate._complete  # TODO don't use private property
        raw_msg = bytes([peer_messages.PeerMsg.BITFIELD])
        raw_msg += raw_pieces.tobytes()
        await self._peer_stream.send_message(raw_msg)

    async def send_choke(self) -> None:
        raw_msg = bytes([peer_messages.PeerMsg.CHOKE])
        await self._peer_stream.send_message(raw_msg)

    async def send_unchoke(self) -> None:
        raw_msg = bytes([peer_messages.PeerMsg.UNCHOKE])
        await self._peer_stream.send_message(raw_msg)

    async def sending_loop(self) -> None:
        assert self._peer_id_and_state is not None
        assert self._receive_outgoing_data is not None
        logger.debug("About to send bitfield to {!r}".format(self._peer_id_and_state[0]))
        await self.send_bitfield()
        logger.debug("Sent bitfield to {!r}".format(self._peer_id_and_state[0]))
        while True:
            logging.debug("sending_loop")
            command, data = "keepalive", None
            with trio.move_on_after(KEEPALIVE_SECONDS):
                command, data = await self._receive_outgoing_data.receive()
            if command == "blocks_to_request":
                for block in data:
                    assert isinstance(block, Block)
                    raw_msg = bytes([peer_messages.PeerMsg.REQUEST])
                    raw_msg += (block.piece_index).to_bytes(4, byteorder="big")
                    raw_msg += (block.block_start).to_bytes(4, byteorder="big")
                    raw_msg += (block.block_length).to_bytes(4, byteorder="big")
                    logger.debug(
                        "Pre-send REQUEST for {} from {!r}".format(
                            (block.piece_index, block.block_start, block.block_length),
                            self._peer_id_and_state[0],
                        )
                    )
                    await self._peer_stream.send_message(raw_msg)
                    logger.debug(
                        "Sent REQUEST for {} from {!r}".format(
                            (block.piece_index, block.block_start, block.block_length),
                            self._peer_id_and_state[0],
                        )
                    )
            elif command == "block_to_upload":
                assert isinstance(data, BlockForPeer)
                raw_msg = bytes([peer_messages.PeerMsg.PIECE])
                raw_msg += (data.block.piece_index).to_bytes(4, byteorder="big")
                raw_msg += (data.block.block_start).to_bytes(4, byteorder="big")
                raw_msg += data.data
                logger.debug(
                    "Pre-send PIECE {} to {!r}".format(data.block, self._peer_id_and_state[0])
                )
                await self._peer_stream.send_message(raw_msg)
                logger.debug("Sent PIECE {} to {!r}".format(data.block, self._peer_id_and_state[0]))
            elif command == "announce_have_piece":
                raw_msg = bytes([peer_messages.PeerMsg.HAVE])
                raw_msg += (data).to_bytes(4, byteorder="big")
                logger.debug("Pre-send HAVE {} to {!r}".format(data, self._peer_id_and_state[0]))
                await self._peer_stream.send_message(raw_msg)
                logger.debug("Sent HAVE {} to {!r}".format(data, self._peer_id_and_state[0]))
            elif command == "choke":
                logger.debug("Pre-send CHOKE to {!r}".format(self._peer_id_and_state[0]))
                await self.send_choke()
                logger.debug("Sent CHOKE to {!r}".format(self._peer_id_and_state[0]))
            elif command == "unchoke":
                logger.debug("Pre-send UNCHOKE to {!r}".format(self._peer_id_and_state[0]))
                await self.send_unchoke()
                logger.debug("Sent UNCHOKE to {!r}".format(self._peer_id_and_state[0]))
            elif command == "keepalive":
                logger.debug("Pre-send KEEPALIVE to {!r}".format(self._peer_id_and_state[0]))
                await self._peer_stream.send_keepalive()
                logger.debug("Sent KEEPALIVE to {!r}".format(self._peer_id_and_state[0]))

            else:
                logger.warning(
                    "PeerEngine for {!r} received unsupported message from Engine: {}".format(
                        self._peer_id_and_state[0], (command, data)
                    )
                )


async def start_peer_engine(
    eng: engine.Engine,
    peer_address: peer_state.PeerAddress,
    stream: trio.SocketStream,
    initiate: bool = True,
) -> None:
    """
    Find (or create) queues for relevant stream, and create PeerEngine.
    """
    peer_engine = PeerEngine(
        eng, peer_address, None, stream, send_peer_msg_to_engine=eng.peer_messages
    )
    await peer_engine.run(initiate=True)


def make_handler(eng: engine.Engine) -> Callable[[trio.SocketStream], Awaitable[None]]:
    async def handler(stream: trio.SocketStream) -> None:
        peer_address = None
        try:
            peer_info = stream.socket.getpeername()
            ip: bytes = peer_info[0]
            port: int = peer_info[1]
            peer_address = peer_state.PeerAddress(ip, port)
            logger.debug("Received incoming peer connection from {}".format(peer_address))
            await start_peer_engine(eng, peer_address, stream, initiate=False)
        except Exception as e:  # TODO this might be too general
            logger.warning(
                "Failed to maintain peer connection to {} because of {}".format(
                    peer_address or "<unknown>", e
                )
            )

    return handler


async def make_standalone(eng: engine.Engine, peer_address: peer_state.PeerAddress) -> None:
    logger.debug("Starting outgoing peer connection to {}".format(peer_address))
    stream: trio.SocketStream | None = None
    try:
        stream = await trio.open_tcp_stream(peer_address.ip, peer_address.port)
        await start_peer_engine(eng, peer_address, stream, initiate=True)
    except Exception as e:  # TODO this might be too general
        logger.warning(
            "Failed to maintain peer connection to {} because of {}".format(peer_address, e)
        )
        if stream:
            await stream.aclose()
