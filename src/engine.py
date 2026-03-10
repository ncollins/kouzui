import collections
from collections.abc import Iterable
import functools
import hashlib
import io
import logging
import math
from pathlib import Path
import random
from enum import StrEnum
from typing import Any

import bitarray
import trio

import bencode
import display
import file_manager
import peer_messages
import peer_connection
import requests
import peer_state
from token_bucket import TokenBucket
from torrent import TorrentInfo, TorrentState, generate_peer_id
import tracker

from config import Config
from internal_messages import (
    AllPiecesWritten,
    BlockToRead,
    CompletePieceToWrite,
    WriteConfirmation,
)
from peer_messages import (
    Choke,
    Have,
    Piece,
    PeerMessage,
    Request,
    Unchoke,
    Bitfield,
    Interested,
    NotInterested,
    PeerConnectionStatus,
    PeerHandshakeSuccess,
    PeerConnectionShutdown,
    PeerConnectionError,
)
from peer_state import PeerState
from shared_types import Block, PeerAddress, PeerId

logger = logging.getLogger("engine")


def _pick_random_one_in_bitarray(b: bitarray.bitarray) -> int | None:
    """
    For a bitarary, b, this picks a random index, i, such that
    b[i] == 1.

    It does this by picking a random starting index and searching forwards
    until it finds an entry equal to 1. If that fails then it searches
    backwards from the starting index.

    Returns None if it can't find an element equal to 1.

    >>> _pick_random_one_in_bitarray(bitarray.bitarray([1,0,0,0]))
    0
    >>> _pick_random_one_in_bitarray(bitarray.bitarray([0,0,0,1]))
    3
    >>> _pick_random_one_in_bitarray(bitarray.bitarray([0,0,0,0])) is None
    True
    """
    n = len(b)
    start = random.randint(0, n - 1)
    # look at tail
    try:
        i = b.index(True, start)
        return i
    except ValueError:
        pass
    # look at head
    try:
        i = b.index(True, 0, start)
        return i
    except ValueError:
        return None


class StatField(StrEnum):
    REQUESTS_IN = "requests_in"
    REQUESTS_OUT = "requests_out"
    BLOCKS_IN = "blocks_in"
    BLOCKS_OUT = "blocks_out"


class Engine(object):
    def __init__(
        self,
        *,
        torrent_info: TorrentInfo,
        torrent_state: TorrentState,
        send_to_file_manager: trio.MemorySendChannel[
            CompletePieceToWrite | AllPiecesWritten | BlockToRead
        ],
        receive_from_file_manager: trio.MemoryReceiveChannel[
            WriteConfirmation | tuple[PeerId, Piece]
        ],
        cfg: Config,
        auto_shutdown: bool = False,
    ) -> None:
        self._cfg = cfg
        self._auto_shutdown: bool = auto_shutdown
        self._torrent_info: TorrentInfo = torrent_info
        self._torrent_state: TorrentState = torrent_state
        # interact with self
        self._peers_without_connection: tuple[
            trio.MemorySendChannel[PeerAddress],
            trio.MemoryReceiveChannel[PeerAddress],
        ] = trio.open_memory_channel(self._cfg.internal_queue_size)
        # interact with FileManager
        self._send_to_file_manager: trio.MemorySendChannel[
            CompletePieceToWrite | AllPiecesWritten | BlockToRead
        ] = send_to_file_manager
        self._receive_from_file_manager: trio.MemoryReceiveChannel[
            WriteConfirmation | tuple[PeerId, Piece]
        ] = receive_from_file_manager
        # interact with peer connections
        self._msg_from_peer: tuple[
            trio.MemorySendChannel[tuple[PeerId, PeerConnectionStatus | PeerMessage]],
            trio.MemoryReceiveChannel[tuple[PeerId, PeerConnectionStatus | PeerMessage]],
        ] = trio.open_memory_channel(self._cfg.internal_queue_size)
        # queues for sending TO peers are initialized on a per-peer basis
        self._peers: dict[PeerId, peer_state.PeerState] = dict()
        # data received but not written to disk
        self._received_blocks: dict[int, tuple[bitarray.bitarray, bytearray]] = dict()
        self.requests = requests.RequestManager()
        self._stats: dict[StatField, int] = {f: 0 for f in StatField}

        if self._cfg.max_outgoing_bytes_per_second is None:
            self.token_bucket: TokenBucket | None = None
        else:
            self.token_bucket = TokenBucket(self._cfg.max_outgoing_bytes_per_second)

    def _inc_stats(self, field: StatField) -> None:
        self._stats[field] += 1
        logger.debug(f"stats updated: {self._stats}")

    @property
    def peer_messages(
        self,
    ) -> trio.MemorySendChannel[tuple[PeerId, PeerConnectionStatus | PeerMessage]]:
        return self._msg_from_peer[0]

    async def run(self) -> None:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.control_loop)
            nursery.start_soon(self.peer_clients_loop)
            nursery.start_soon(self.peer_server_loop)
            nursery.start_soon(self.tracker_loop)
            nursery.start_soon(self.peer_messages_loop)
            nursery.start_soon(self.file_manager_loop)
            nursery.start_soon(self.info_loop)
            nursery.start_soon(self.choking_loop)
            nursery.start_soon(
                self.delete_stale_requests_loop, self._cfg.delete_stale_requests_seconds
            )
            if self.token_bucket is not None:
                nursery.start_soon(self.token_bucket.loop)

    async def control_loop(self) -> None:
        while True:
            complete_peers = [p.get_pieces().all for p in self._peers.values()]
            if (
                self._auto_shutdown
                and all(complete_peers)
                and self._torrent_state.completed_pieces.all()
            ):  # TODO remove private variable access
                await self._send_to_file_manager.send(AllPiecesWritten())
                await trio.sleep(1)
                raise SystemExit(0)
            elif self._torrent_state.completed_pieces.all():  # TODO remove private variable access
                await self._send_to_file_manager.send(AllPiecesWritten())
            await trio.sleep(2)

    async def info_loop(self) -> None:
        while True:
            num_unwritten_blocks = len(self._received_blocks.items())
            outstanding_requests = self.requests.size
            logger.info(f"stats = {self._stats}")
            logger.info(
                f"{num_unwritten_blocks} unwritten blocks, {outstanding_requests} outstanding_requests, {sum(self._torrent_state.completed_pieces)}/{len(self._torrent_state.completed_pieces)} complete pieces"
            )
            # TODO 2026-03-01: Fixes were made to this if statement and logging, but as the
            # block is not triggered by the current integration tests it will need to be
            # verified at some point in the future.
            if (
                sum(self._torrent_state.completed_pieces)
                / len(self._torrent_state.completed_pieces)
                > 0.97
            ) or (
                len(self._torrent_state.completed_pieces)
                - sum(self._torrent_state.completed_pieces)
                < 2
            ):
                logger.info(f"Outstanding requests = {self.requests._requests}")
                unwritten_blocks = [
                    (i, b, len(data)) for i, (b, data) in self._received_blocks.items()
                ]
                logger.info(f"Unwritten blocks: {unwritten_blocks}")
            channels: list[trio.MemorySendChannel[Any] | trio.MemoryReceiveChannel[Any]] = [
                self._peers_without_connection[0],
                self._send_to_file_manager,
                self._receive_from_file_manager,
                self._msg_from_peer[0],
            ]
            logger.info(f"Memory channels {[c.statistics() for c in channels]}")
            logger.info(f"Alive peers {self._peers.keys()}")
            display.print_peers(self._torrent_info, self._torrent_state, self._peers)
            await trio.sleep(1)

    async def tracker_loop(self) -> None:
        new = True
        while True:
            logger.debug("tracker_loop")
            start_time = trio.current_time()
            event = b"started" if new else None
            raw_tracker_info = await tracker.query(
                self._torrent_info, self._torrent_state, event, cfg=self._cfg
            )
            tracker_info = bencode.parse_value(io.BytesIO(raw_tracker_info))
            if not isinstance(tracker_info, collections.OrderedDict):
                raise Exception(f"Invalid tracker info: {tracker_info!r}")
            # update peers
            # TODO we could recieve peers in a different format
            logger.info(f"tracker_info = {tracker_info}")
            try:
                peer_ips_and_ports = tracker.parse_peers(
                    tracker_info[b"peers"], listening_port=self._torrent_state.listening_port
                )
                peers = [(address, peer_id) for address, peer_id in peer_ips_and_ports]
                logger.info(f"Found peers from tracker: {peers}")
                await self.update_peers(peers)
            except ValueError as e:
                logger.error(f"Error passing peers: {e}")

            # update other info:
            # self._torrent_state.completed_pieces_peers = tracker_info['complete']
            # self._torrent_info.incomplete_peers = tracker_info['incomplete']
            # self._torrent_info.interval = int(tracker_info['interval'])
            # tell tracker the new interval
            await trio.sleep_until(start_time + self._torrent_info.interval)
            new = False

    async def peer_server_loop(self) -> None:
        await trio.serve_tcp(
            peer_connection.make_handler(
                info_hash=self._torrent_info.info_hash,
                self_peer_id=self._torrent_state.peer_id,
                token_bucket=self.token_bucket,
                channel_to_engine=self._msg_from_peer[0],
                cfg=self._cfg,
            ),
            self._torrent_state.listening_port,
        )

    async def peer_clients_loop(self) -> None:
        """
        Start up clients for new peers that are not from the serve.
        """
        logger.debug("starting peer_clients_loop")
        async with trio.open_nursery() as nursery:
            while True:
                logger.debug("peer_clients_loop")
                address = await self._peers_without_connection[1].receive()
                make_standalone = functools.partial(
                    peer_connection.make_standalone,
                    info_hash=self._torrent_info.info_hash,
                    self_peer_id=self._torrent_state.peer_id,
                    token_bucket=self.token_bucket,
                    channel_to_engine=self._msg_from_peer[0],
                    peer_address=address,
                    cfg=self._cfg,
                )
                nursery.start_soon(make_standalone)

    async def update_peers(self, peers: Iterable[tuple[PeerAddress, PeerId | None]]) -> None:
        for address, peer_id in peers:
            if peer_id in self._peers:
                logger.info(f"Peer already exists: {peer_id!r}")
            else:
                logger.info(f"Adding new peer to queue: {address!r} / {peer_id!r}")
                await self._peers_without_connection[0].send(address)

    def _blocks_from_index(self, index: int) -> set[Block]:
        piece_length = self._torrent_info.piece_length(index)
        block_length = min(piece_length, self._cfg.block_size)
        begin_indexes = list(range(0, piece_length, block_length))
        return set(
            Block(
                piece_index=index,
                block_start=begin,
                block_length=min(block_length, piece_length - begin),
            )
            for begin in begin_indexes
        )

    async def update_peer_requests(self) -> None:
        # Look at what the client has, what the peers have
        # and update the requested pieces for each peer.
        if self._torrent_state.completed_pieces.all():
            logger.info("Not making new requests, download is complete")
            return
        if not self._peers:
            logger.info("Not making new requests as there are no peers")
            return
        for address, peer in self._peers.items():
            if peer.is_client_choked:
                continue
            # TODO don't read private field of another object
            targets = (~self._torrent_state.completed_pieces) & peer._pieces
            target_index = _pick_random_one_in_bitarray(targets)
            if target_index is not None:
                logger.info(
                    f"{address!r}: self any? {self._torrent_state.completed_pieces.any()}, peer any? {peer._pieces.any()}, target_index = {target_index}"
                )
                existing_requests = self.requests.existing_requests_for_peer(address)
                if len(existing_requests) > self._cfg.max_outstanding_requests_per_peer:
                    logger.info(
                        f"{address!r}: Not making new requests: {len(existing_requests)} existing"
                    )
                    new_requests: set[Block] = set()
                else:
                    suggested_requests = self._blocks_from_index(target_index)
                    new_requests = suggested_requests.difference(existing_requests)
                    logger.info(
                        f"{address!r}: {len(suggested_requests)} suggested requests, {len(existing_requests)} existing"
                    )
                logger.info(f"{address!r}: new_requests = <length={len(new_requests)}>")
                if new_requests:
                    for block in new_requests:
                        self.requests.add_request(address, block)
                        self._inc_stats(StatField.REQUESTS_OUT)
                        await peer.send_channel.send(Request(block=block))
            else:
                logger.info(f"No target pieces for {address!r}")

    async def handle_peer_connection_status(
        self, peer_id: PeerId, connection_status: PeerConnectionStatus
    ) -> None:
        match connection_status:
            case PeerHandshakeSuccess(peer_channel=peer_channel) if peer_id in self._peers:
                logger.info(f"{peer_id!r} already exists, closing the channel")
                await connection_status.peer_channel.aclose()
            case PeerHandshakeSuccess(peer_channel=peer_channel):
                # Send the Bitfield before adding the peer_id, peer_state to the dictionary
                # to ensure that it gets sent before any other messages
                await peer_channel.send(Bitfield(pieces=self._torrent_state.completed_pieces))
                peer_state = PeerState(
                    peer_id, self._torrent_info.num_pieces, send_channel=peer_channel
                )
                self._peers[peer_id] = peer_state
            case PeerConnectionShutdown() | PeerConnectionError():
                logging.info(
                    f"removing {peer_id!r} from Engine._peers because of {connection_status}"
                )
                self._peers.pop(peer_id, None)
            case _:
                assert False

    async def handle_peer_message(self, peer_id: PeerId, msg: PeerMessage) -> None:
        logger.info(f"Received {msg} from {peer_id!r}")
        peer_state = self._peers.get(peer_id)
        if peer_id not in self._peers:
            logger.info(f"did not handle {msg} because peer_id={peer_id!r} no longer exists")
            return

        peer_state = self._peers[peer_id]
        match msg:
            case Choke():
                peer_state.choke_us()
            case Unchoke():
                peer_state.unchoke_us()
            case Interested():
                logger.warning(f"{msg} is not implemented")
            case NotInterested():
                logger.warning(f"{msg} is not implemented")
            case Have(piece_index=piece_index):
                peer_state.get_pieces()[piece_index] = True
            case Bitfield(pieces=pieces):
                # TODO would be useful to log what percentage of the file the peer has
                peer_state.set_pieces(pieces)
            case Request(block=block):
                self._inc_stats(StatField.REQUESTS_IN)
                if peer_state.is_peer_choked:
                    logger.warning(f"{peer_state.peer_id!r} requested {block} but peer is choked")
                elif self._torrent_state.completed_pieces[block.piece_index]:
                    await self._send_to_file_manager.send(
                        BlockToRead(peer_id=peer_state.peer_id, block=block)
                    )
                else:
                    logger.warning(
                        f"{peer_state.peer_id!r} requested {block} but piece is incomplete"
                    )
            case Piece(piece_index=piece_index, block_start=block_start, data=data):
                self._inc_stats(StatField.BLOCKS_IN)
                peer_state.inc_download_counters()
                await self.handle_block_received(piece_index, block_start, data)
            case peer_messages.MessageTypeByte.CANCEL:
                logger.warning(f"{msg} is not implemented")
            case _:
                assert False

    async def handle_block_received(self, index: int, begin: int, data: bytes) -> None:
        if index not in self._received_blocks:
            piece_length = self._torrent_info.piece_length(index)
            completed_blocks = bitarray.bitarray(math.ceil(piece_length / self._cfg.block_size))
            completed_blocks.setall(False)
            piece_data = bytearray(piece_length)
            self._received_blocks[index] = (completed_blocks, piece_data)
        else:
            completed_blocks = self._received_blocks[index][0]
            piece_data = self._received_blocks[index][1]
        block_index = begin // self._cfg.block_size
        completed_blocks[block_index] = True
        piece_data[begin : begin + len(data)] = data
        if completed_blocks.all():
            piece_info = self._torrent_info.piece_info(index)
            complete_piece = bytes(piece_data)
            if hashlib.sha1(complete_piece).digest() == piece_info.sha1hash:
                self._received_blocks.pop(index)  # TODO is this ordering significant?
                await self._send_to_file_manager.send(
                    CompletePieceToWrite(index=index, data=complete_piece)
                )
            else:
                self._received_blocks.pop(index)
                self.requests.delete_all_for_piece(index)
                logger.warning(f"sha1hash does not match for index {index}")

    async def peer_messages_loop(self) -> None:
        while True:
            logger.debug("peer_messages_loop")
            peer_id, msg = await self._msg_from_peer[1].receive()
            logger.debug(f"Engine recieved peer message from {peer_id!r}")
            match msg:
                case PeerConnectionStatus():
                    await self.handle_peer_connection_status(peer_id, msg)
                case PeerMessage():
                    await self.handle_peer_message(peer_id, msg)
                case _:
                    assert False
            await self.update_peer_requests()

    async def announce_have_piece(self, index: int) -> None:
        peers = (
            self._peers.copy()
        )  # shallow copy, but that should be enough as we're not modifying the PeerState objects
        for _peer_id, peer_s in peers.items():
            await peer_s.send_channel.send(Have(piece_index=index))

    async def file_manager_loop(self) -> None:
        while True:
            logger.debug("file_manager_loop")
            msg = await self._receive_from_file_manager.receive()
            if isinstance(msg, WriteConfirmation):
                self.requests.delete_all_for_piece(msg.index)
                # NB - update the _complete vector first to guarantee that new clients get
                # the most upto date bitfield (they may also get a redundant HAVE message)
                self._torrent_state.completed_pieces[msg.index] = (
                    True  # TODO remove private property access
                )
                await self.announce_have_piece(msg.index)
                await self.update_peer_requests()
            else:
                peer_id, piece = msg
                self._inc_stats(StatField.BLOCKS_OUT)
                if peer_id in self._peers:
                    p_state = self._peers[peer_id]
                    p_state.inc_upload_counters()
                    await p_state.send_channel.send(piece)
                else:
                    logger.info(
                        f"dropped piece {piece} for {peer_id!r} because peer no longer exists"
                    )

    async def choking_loop(self) -> None:
        period = 0
        optimistic_unchoke = None
        while True:
            await trio.sleep(10)
            peers = [
                (peer_id, peer_s.get_20_second_rolling_download_count())
                for peer_id, peer_s in self._peers.items()
            ]
            if period == 0 and peers:
                optimistic_unchoke = random.choice(peers)[0]
            peers = sorted(peers, key=lambda x: x[1], reverse=True)
            logger.info(f"Peers ordered by successful downloads in last 20 seconds: {peers}")
            # First X are unchoked
            # Rest are choked
            unchoke = set(p[0] for p in peers[: self._cfg.num_unchoked_peers])
            choke = set(p[0] for p in peers[self._cfg.num_unchoked_peers :])
            logger.info(f"unchoke = {unchoke}, choke = {choke}")
            if optimistic_unchoke:
                unchoke.add(optimistic_unchoke)
                choke.discard(optimistic_unchoke)
            for p_id in unchoke:
                if p_id in self._peers:  # protect against state change while putting in queue
                    p_state = self._peers[p_id]
                    alert = p_state.unchoke_them()
                    p_state.reset_rolling_download_count()
                    if alert == peer_state.ChokeAlert.ALERT:
                        await p_state.send_channel.send(Unchoke())
            for p_id in choke:
                if p_id in self._peers:  # protect against state change while putting in queue
                    p_state = self._peers[p_id]
                    alert = p_state.choke_them()
                    p_state.reset_rolling_download_count()
                    if alert == peer_state.ChokeAlert.ALERT:
                        await p_state.send_channel.send(Choke())
            # update period
            period = (period + 1) % 3  # rotate period every 30 seconds

    async def delete_stale_requests_loop(self, seconds: int) -> None:
        while True:
            await trio.sleep(seconds)
            count = self.requests.delete_older_than(seconds=seconds)
            logging.info(f"Deleted {count} stale requests (older than {seconds} seconds)")


def run(
    torrent_info: TorrentInfo,
    *,
    directory: Path,
    listening_port: int,
    cfg: Config,
    auto_shutdown: bool,
) -> None:
    try:
        completed_pieces = bitarray.bitarray(torrent_info.num_pieces)
        completed_pieces.setall(False)
        torrent_state = TorrentState(
            left=torrent_info.file_length,
            file_path=directory / torrent_info.torrent_name,
            listening_port=listening_port,
            peer_id=generate_peer_id(),
            completed_pieces=completed_pieces,
        )

        # create FileManager and check hashes if file already exists
        file_wrapper = file_manager.FileWrapper(
            torrent_info=torrent_info, file_path=torrent_state.file_path
        )
        existing_hashes = file_wrapper.create_file_or_return_hashes()

        if existing_hashes:
            for index, h in enumerate(existing_hashes):
                piece_info = torrent_info.piece_info(index)
                if piece_info.sha1hash == h:
                    torrent_state.completed_pieces[index] = True

        s_to_file_manager, r_from_engine = trio.open_memory_channel[
            CompletePieceToWrite | AllPiecesWritten | BlockToRead
        ](cfg.internal_queue_size)
        s_to_engine, r_from_file_manager = trio.open_memory_channel[
            WriteConfirmation | tuple[PeerId, Piece]
        ](cfg.internal_queue_size)

        eng = Engine(
            torrent_info=torrent_info,
            torrent_state=torrent_state,
            send_to_file_manager=s_to_file_manager,
            receive_from_file_manager=r_from_file_manager,
            cfg=cfg,
            auto_shutdown=auto_shutdown,
        )

        async def run() -> None:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(
                    functools.partial(
                        file_manager.file_manager_loop,
                        file_wrapper=file_wrapper,
                        receive_from_engine=r_from_engine,
                        send_to_engine=s_to_engine,
                    )
                )
                nursery.start_soon(eng.run)

        trio.run(run)
    except KeyboardInterrupt:
        print()
        print("Shutting down without cleanup...")
