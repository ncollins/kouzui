from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, AsyncGenerator
import functools
from typing import TypeVar, TYPE_CHECKING

import trio

if TYPE_CHECKING:
    import token_bucket
from config import Config
from peer_messages import (
    PeerMessage,
    KeepAlive,
    parse_message_with_length,
    CloseConnectionOrder,
    PeerConnectionStatus,
    PeerHandshakeSuccess,
    PeerConnectionError,
    PeerConnectionShutdown,
)
from shared_types import PeerAddress, PeerId

logger = logging.getLogger("peer")

X = TypeVar("X")
K = TypeVar("K")


async def insert_keepalive(
    channel: trio.MemoryReceiveChannel[X], keepalive_value: K, seconds: int
) -> AsyncGenerator[X | K, None]:
    msg: X | None = None
    while True:
        with trio.move_on_after(seconds):
            msg = await channel.receive()
        match msg:
            case None:
                yield keepalive_value
            case msg:
                yield msg


def _build_handshake(info_hash: bytes, peer_id: PeerId) -> bytes:
    return b"\x13BitTorrent protocol" + (b"\0" * 8) + info_hash + peer_id


def _parse_handshake(data: bytes, info_hash: bytes, expected_peer_id: PeerId | None) -> PeerId:
    if len(data) < 20 + 8 + 20 + 20:
        raise HandshakeError("Handshake data: wrong length", data)
    header = data[:20]
    _reserved_bytes = data[20 : 20 + 8]
    sha1hash = data[20 + 8 : 20 + 8 + 20]
    peer_id = data[20 + 8 + 20 : 20 + 8 + 20 + 20]
    if not (header == b"\x13BitTorrent protocol"):
        raise HandshakeError("Handshake data: wrong header", header)
    if not (sha1hash == info_hash):
        raise HandshakeError("Handshake data: wrong hash", sha1hash)
    if expected_peer_id:
        if not expected_peer_id == peer_id:
            raise HandshakeError("Handshake data: peer_id does not match", peer_id)
    return peer_id


def make_read_fixed_length_machine(length: int) -> Callable[[bytes], tuple[bytes, bytes] | None]:
    data = b""

    def read_fixed_length_machine(input: bytes) -> tuple[bytes, bytes] | None:
        nonlocal data
        if input == b"":
            raise Exception("EOF")
        data += input
        logger.info(f"data = {data!r}, input = {input!r}, len(data) = {len(data)}")

        if len(data) >= length:
            return data[:length], data[length:]
        else:
            return None

    return read_fixed_length_machine


def make_message_machine(initial_data: bytes) -> Callable[[bytes], list[PeerMessage]]:
    data = initial_data

    def message_machine(input: bytes) -> list[PeerMessage]:
        nonlocal data
        data += input
        messages: list[PeerMessage] = []
        while (parsed_message := parse_message_with_length(data)) is not None:
            msg, remaining_data = parsed_message
            data = remaining_data
            messages.append(msg)
        return messages

    return message_machine


class HandshakeError(Exception):
    def __init__(self, reason: str, data: bytes) -> None:
        self.reason = reason
        self.data = data


class PeerShutdown(Exception):
    pass


async def _receive_handshake(
    socket: trio.SocketStream,
    peer_address: PeerAddress,
    info_hash: bytes,
    expected_peer_id: PeerId | None,
    cfg: Config,
) -> tuple[PeerId, bytes]:
    read_handshake_data_machine = make_read_fixed_length_machine(68)
    logger.debug(f"Starting to receive handshake on {socket}")
    while True:
        next_input = await socket.receive_some(cfg.stream_chunk_size)
        logger.info(
            f"Handshake data from {socket.socket.getpeername()}: {next_input!r} {len(next_input)}"
        )
        match read_handshake_data_machine(next_input):
            case None:
                continue
            case (handshake_data, leftover):
                logger.debug(f"Handshake data = {handshake_data!r}")
                peer_id = _parse_handshake(handshake_data, info_hash, expected_peer_id)
                logger.debug(f"Received handshake from {peer_address}/{peer_id!r}")
                return peer_id, leftover


async def _send_handshake(
    socket: trio.SocketStream,
    peer_address: PeerAddress,
    info_hash: bytes,
    self_peer_id: PeerId,
) -> None:
    handshake_data = _build_handshake(info_hash, self_peer_id)
    logger.debug("Sending handshake")
    logger.debug(f"Outgoing handshake = {handshake_data!r}")
    logger.debug(f"Length of outgoing handshake {len(handshake_data)}")
    await socket.send_all(handshake_data)
    logger.debug(f"Sent handshake to {peer_address}")


async def _receiving_loop(
    *,
    socket: trio.SocketStream,
    send_to_engine: trio.MemorySendChannel[tuple[PeerId, PeerConnectionStatus | PeerMessage]],
    peer_id: PeerId,
    existing_socket_data: bytes,
    cfg: Config,
) -> None:
    message_machine = make_message_machine(existing_socket_data)
    while True:
        next_input = await socket.receive_some(cfg.stream_chunk_size)
        if next_input != b"":
            logger.debug(f"received_message: Got {len(next_input)} from {socket}")
        else:
            logger.debug(f"empty data, about to raise EOF from {socket}")
            raise Exception("EOF")
        messages = message_machine(next_input)
        for msg in messages:
            match msg:
                case KeepAlive():
                    pass
                case _:
                    await send_to_engine.send((peer_id, msg))


async def _sending_loop(
    *,
    socket: trio.SocketStream,
    receive_from_engine: trio.MemoryReceiveChannel[PeerMessage | CloseConnectionOrder],
    token_bucket: token_bucket.TokenBucket | None,
    peer_id: PeerId,
    cfg: Config,
) -> None:
    async for msg in insert_keepalive(receive_from_engine, KeepAlive(), cfg.keepalive_seconds):
        logging.debug("sending_loop")
        match msg:
            case PeerMessage():
                logger.debug(f"Pre-send {msg} from {peer_id!r}")
                data = msg.to_bytes()
                if token_bucket is not None:
                    await token_bucket.wait_for_approval(len(data))
                await socket.send_all(data)
                logger.debug(f"Sent {msg} from {peer_id!r}")
            case CloseConnectionOrder():
                raise Exception(f"{peer_id!r} received {CloseConnectionOrder()}")


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
    peer_id = None
    leftover = b""
    try:
        # Do handshakes before starting main loops
        if initiate:
            await _send_handshake(stream, peer_address, info_hash, self_peer_id)
            peer_id, leftover = await _receive_handshake(stream, peer_address, info_hash, None, cfg)
        else:
            peer_id, leftover = await _receive_handshake(stream, peer_address, info_hash, None, cfg)
            await _send_handshake(stream, peer_address, info_hash, self_peer_id)

        channels: tuple[
            trio.MemorySendChannel[PeerMessage | CloseConnectionOrder],
            trio.MemoryReceiveChannel[PeerMessage | CloseConnectionOrder],
        ] = trio.open_memory_channel(cfg.internal_queue_size)
        await channel_to_engine.send((peer_id, PeerHandshakeSuccess(peer_channel=channels[0])))

        async with trio.open_nursery() as nursery:
            nursery.start_soon(
                functools.partial(
                    _receiving_loop,
                    socket=stream,
                    send_to_engine=channel_to_engine,
                    peer_id=peer_id,
                    existing_socket_data=leftover,
                    cfg=cfg,
                )
            )
            nursery.start_soon(
                functools.partial(
                    _sending_loop,
                    receive_from_engine=channels[1],
                    socket=stream,
                    token_bucket=token_bucket,
                    peer_id=peer_id,
                    cfg=cfg,
                )
            )
    except PeerShutdown:
        if peer_id is not None:
            await channel_to_engine.send((peer_id, PeerConnectionShutdown()))
        logger.exception(
            f"Exception raised in peer connection, it will be closed ({peer_address} / {peer_id!r}) and the exception re-raised."
        )
    except Exception as e:
        # TODO 2026-03-05: This exception handling and logging could be tidied up. In  particular, an Exception("EOF") when the
        # peer closes the connection isn't really a problem. Currently the re-raised exception is caught at a later point and a WARNING
        # message is logged, but it doesn't provide details.
        if peer_id is not None:
            await channel_to_engine.send((peer_id, PeerConnectionError(exception=e)))
        logger.exception(
            f"Exception raised in peer connection, it will be closed ({peer_address} / {peer_id!r}) and the exception re-raised."
        )
        raise e


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
        async with await trio.open_tcp_stream(peer_address.ip, peer_address.port) as stream:
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
