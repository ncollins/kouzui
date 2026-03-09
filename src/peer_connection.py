from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Optional, TYPE_CHECKING

import trio

if TYPE_CHECKING:
    import token_bucket
from config import Config
from peer_messages import (
    PeerMessage,
    parse_message,
    CloseConnectionOrder,
    PeerConnectionStatus,
    PeerHandshakeSuccess,
    PeerConnectionError,
    PeerConnectionShutdown,
)
from shared_types import PeerAddress, PeerId

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
        self,
        stream: trio.SocketStream,
        token_bucket: token_bucket.TokenBucket | None = None,
        *,
        cfg: Config,
    ):
        self._stream: trio.SocketStream = stream
        self._msg_data: bytes = b""
        self._token_bucket = token_bucket
        self._cfg = cfg

    async def receive_handshake(self) -> PeerId:
        logger.debug(f"Starting to received handshake on {self._stream}")
        data = None
        while len(self._msg_data) < 68:
            data = await self._stream.receive_some(self._cfg.stream_chunk_size)
            if data == b"":
                logger.debug(f"empty data in handshake, about to raise EOF from {self._stream}")
                raise Exception("EOF in handshake")
            logger.debug(
                f"Initial incoming handshake data from {self._stream.socket.getpeername()}: {data!r}"
            )
            self._msg_data += data
        handshake_data = self._msg_data[:68]
        self._msg_data = self._msg_data[68:]
        logger.debug(f"Final incoming handshake data {data!r}")
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
                    logger.debug(f"Parsed message of length {msg_length} from {self._stream}")

    async def receive_message(self) -> list[tuple[int, bytes]]:
        logger.debug(f"Called receive_message for {self._stream}")
        while True:
            messages = self._parse_msg_data()
            if messages:
                return messages
            else:
                data = await self._stream.receive_some(self._cfg.stream_chunk_size)
                if data != b"":
                    logger.debug(f"received_message: Got {len(data)} from {self._stream}")
                else:
                    logger.debug(f"empty data, about to raise EOF from {self._stream}")
                    raise Exception("EOF")
                self._msg_data += data

    async def send_message(self, msg: bytes) -> None:
        message_length = len(msg)
        data = message_length.to_bytes(4, byteorder="big") + msg
        logger.debug(f"Pre-send message of length {message_length} on {self._stream}")
        if self._token_bucket is not None:
            # TODO 2026-03-04: consider moving this into a single call to the token bucket
            while not self._token_bucket.check_and_decrement(len(data)):
                logger.debug("Token bucket is empty waiting 0.1s")
                await trio.sleep(self._token_bucket.update_period)
        await self._stream.send_all(data)
        logger.debug(f"Sent message of length {message_length} on {self._stream}")

    async def send_handshake(self, info_hash: bytes, peer_id: PeerId) -> None:
        handshake_data = b"\x13BitTorrent protocol" + (b"\0" * 8) + info_hash + peer_id
        logger.debug("Sending handshake")
        logger.debug(f"Outgoing handshake = {handshake_data!r}")
        logger.debug(f"Length of outgoing handshake {len(handshake_data)}")
        await self._stream.send_all(handshake_data)
        logger.debug("Sent handshake")

    async def send_keepalive(self) -> None:
        data = (0).to_bytes(4, byteorder="big")
        await self._stream.send_all(data)


class HandshakeError(Exception):
    def __init__(self, reason: str, data: bytes) -> None:
        self.reason = reason
        self.data = data


class PeerShutdown(Exception):
    pass


class PeerEngine(object):
    """
    PeerEngine is initialized with a stream and two queues.
    """

    def __init__(
        self,
        *,
        peer_address: PeerAddress,
        expected_peer_id: PeerId | None,
        stream: trio.SocketStream,
        info_hash: bytes,
        self_peer_id: PeerId,
        token_bucket: token_bucket.TokenBucket | None,
        channel_to_engine: trio.MemorySendChannel[
            tuple[PeerId, PeerConnectionStatus | PeerMessage]
        ],
        cfg: Config,
    ):
        self._cfg = cfg
        self._peer_address: PeerAddress = peer_address
        self._expected_peer_id: PeerId | None = expected_peer_id
        self._peer_id: Optional[PeerId] = None
        self._peer_stream: PeerStream = PeerStream(stream, token_bucket, cfg=self._cfg)
        self._info_hash = info_hash
        self._self_peer_id = self_peer_id
        self._channel_to_engine: trio.MemorySendChannel[
            tuple[PeerId, PeerConnectionStatus | PeerMessage]
        ] = channel_to_engine
        self._receive_outgoing_data: Optional[
            trio.MemoryReceiveChannel[PeerMessage | CloseConnectionOrder]
        ] = None

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

            channels: tuple[
                trio.MemorySendChannel[PeerMessage | CloseConnectionOrder],
                trio.MemoryReceiveChannel[PeerMessage | CloseConnectionOrder],
            ] = trio.open_memory_channel(self._cfg.internal_queue_size)
            self._peer_id = peer_id
            self._receive_outgoing_data = channels[1]
            await self._channel_to_engine.send(
                (peer_id, PeerHandshakeSuccess(peer_channel=channels[0]))
            )
            async with trio.open_nursery() as nursery:
                nursery.start_soon(self.receiving_loop)
                nursery.start_soon(self.sending_loop)
        except PeerShutdown:
            if peer_id is not None:
                await self._channel_to_engine.send((peer_id, PeerConnectionShutdown()))
            logger.exception(
                f"Exception raised in PeerEngine, the PeerEngine will be closed ({self._peer_address} / {peer_id!r}) and the exception re-raised."
            )
        except Exception as e:
            # TODO 2026-03-05: This exception handling and logging could be tidied up. In  particular, an Exception("EOF") when the
            # peer closes the connection isn't really a problem. Currently the re-raised exception is caught at a later point and a WARNING
            # message is logged, but it doesn't provide details.
            if peer_id is not None:
                await self._channel_to_engine.send((peer_id, PeerConnectionError(exception=e)))
            logger.exception(
                f"Exception raised in PeerEngine, the PeerEngine will be closed ({self._peer_address} / {peer_id!r}) and the exception re-raised."
            )
            raise e

    async def receive_handshake(self) -> PeerId:
        # First, receive handshake
        data = await self._peer_stream.receive_handshake()
        logger.debug(f"Handshake data = {data!r}")
        # Second, validation
        if len(data) < 20 + 8 + 20 + 20:
            raise HandshakeError("Handshake data: wrong length", data)
        header = data[:20]
        _reserved_bytes = data[20 : 20 + 8]
        sha1hash = data[20 + 8 : 20 + 8 + 20]
        peer_id = data[20 + 8 + 20 : 20 + 8 + 20 + 20]
        if not (header == b"\x13BitTorrent protocol"):
            raise HandshakeError("Handshake data: wrong header", header)
        if not (sha1hash == self._info_hash):
            raise HandshakeError("Handshake data: wrong hash", sha1hash)
        if self._expected_peer_id:
            if not self._expected_peer_id == peer_id:
                raise HandshakeError("Handshake data: peer_id does not match", peer_id)
        logger.debug(f"Received handshake from {self._peer_address}/{peer_id!r}")
        return peer_id

    async def send_handshake(self) -> None:
        # Handshake
        await self._peer_stream.send_handshake(self._info_hash, self._self_peer_id)
        logger.debug(f"Sent handshake to {self._peer_address}")

    async def receiving_loop(self) -> None:
        assert self._peer_id is not None
        while True:
            logging.debug(f"receiving_loop for {self._peer_id!r}")
            messages = await self._peer_stream.receive_message()
            for length, data in messages:
                logger.debug(f"Received message of length {length} from {self._peer_id!r}")
                if length == 0:
                    # keepalive message
                    pass
                else:
                    peer_message = parse_message(data)
                    logger.debug("Putting message in queue for engine")
                    await self._channel_to_engine.send(
                        (
                            self._peer_id,
                            peer_message,
                        )
                    )

    async def sending_loop(self) -> None:
        assert self._peer_id is not None
        assert self._receive_outgoing_data is not None
        while True:
            logging.debug("sending_loop")
            msg: None | PeerMessage | CloseConnectionOrder = None
            with trio.move_on_after(self._cfg.keepalive_seconds):
                msg = await self._receive_outgoing_data.receive()
            match msg:
                case None:
                    logger.debug(f"Pre-send KEEPALIVE to {self._peer_id!r}")
                    await self._peer_stream.send_keepalive()
                    logger.debug(f"Sent KEEPALIVE to {self._peer_id!r}")
                case PeerMessage():
                    logger.debug(f"Pre-send {msg} from {self._peer_id!r}")
                    await self._peer_stream.send_message(msg.to_bytes())
                    logger.debug(f"Sent {msg} from {self._peer_id!r}")
                case CloseConnectionOrder():
                    raise Exception(f"{self._peer_id!r} received {CloseConnectionOrder()}")


async def start_peer_engine(
    *,
    peer_address: PeerAddress,
    stream: trio.SocketStream,
    info_hash: bytes,
    self_peer_id: PeerId,
    token_bucket: token_bucket.TokenBucket | None,
    channel_to_engine: trio.MemorySendChannel[tuple[PeerId, PeerConnectionStatus | PeerMessage]],
    initiate: bool = True,
    cfg: Config,
) -> None:
    """
    Find (or create) queues for relevant stream, and create PeerEngine.
    """
    peer_engine = PeerEngine(
        peer_address=peer_address,
        expected_peer_id=None,
        stream=stream,
        info_hash=info_hash,
        self_peer_id=self_peer_id,
        token_bucket=token_bucket,
        channel_to_engine=channel_to_engine,
        cfg=cfg,
    )
    await peer_engine.run(initiate=initiate)


def make_handler(
    *,
    info_hash: bytes,
    self_peer_id: PeerId,
    token_bucket: token_bucket.TokenBucket | None,
    channel_to_engine: trio.MemorySendChannel[tuple[PeerId, PeerConnectionStatus | PeerMessage]],
    cfg: Config,
) -> Callable[[trio.SocketStream], Awaitable[None]]:
    async def handler(stream: trio.SocketStream) -> None:
        peer_address = None
        try:
            # NOTE: stream.socket.getpeername() could actually return anything, but for
            # an IPv4 connection it returns an (ip, port) pair
            peer_info = stream.socket.getpeername()
            ip: bytes = peer_info[0].encode()
            port: int = peer_info[1]
            peer_address = PeerAddress(ip=ip, port=port)
            logger.debug(f"Received incoming peer connection from {peer_address}")
            await start_peer_engine(
                peer_address=peer_address,
                stream=stream,
                info_hash=info_hash,
                self_peer_id=self_peer_id,
                token_bucket=token_bucket,
                channel_to_engine=channel_to_engine,
                initiate=False,
                cfg=cfg,
            )
        except Exception as e:  # TODO this might be too general
            logger.warning(
                f"Failed to maintain peer connection to {peer_address or '<unknown>'} because of {e}"
            )

    return handler


async def make_standalone(
    *,
    info_hash: bytes,
    self_peer_id: PeerId,
    token_bucket: token_bucket.TokenBucket | None,
    channel_to_engine: trio.MemorySendChannel[tuple[PeerId, PeerConnectionStatus | PeerMessage]],
    peer_address: PeerAddress,
    cfg: Config,
) -> None:
    logger.debug(f"Starting outgoing peer connection to {peer_address}")
    stream: trio.SocketStream | None = None
    try:
        stream = await trio.open_tcp_stream(peer_address.ip, peer_address.port)
        await start_peer_engine(
            peer_address=peer_address,
            stream=stream,
            info_hash=info_hash,
            self_peer_id=self_peer_id,
            token_bucket=token_bucket,
            channel_to_engine=channel_to_engine,
            initiate=True,
            cfg=cfg,
        )
    except Exception as e:  # TODO this might be too general
        logger.warning(f"Failed to maintain peer connection to {peer_address} because of {e}")
        if stream:
            await stream.aclose()
