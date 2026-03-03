import collections
import hashlib
import io
import logging
import math
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
import torrent as state
import tracker

import config
from internal_messages import (
    AllPiecesWritten,
    BlockToRead,
    CompletePieceToWrite,
    WriteConfirmation,
)
from peer_messages import Choke, Have, Piece, RawPeerMessage, Request, Unchoke
from utility_types import Block

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
        torrent: state.Torrent,
        complete_pieces_to_write: trio.MemorySendChannel[CompletePieceToWrite | AllPiecesWritten],
        write_confirmations: trio.MemoryReceiveChannel[WriteConfirmation],
        blocks_to_read: trio.MemorySendChannel[BlockToRead],
        blocks_for_peers: trio.MemoryReceiveChannel[Piece],
        auto_shutdown: bool = False,
    ) -> None:
        self._auto_shutdown: bool = auto_shutdown
        self._state: state.Torrent = torrent
        # interact with self
        self._peers_without_connection: tuple[
            trio.MemorySendChannel[peer_state.PeerAddress],
            trio.MemoryReceiveChannel[peer_state.PeerAddress],
        ] = trio.open_memory_channel(config.INTERNAL_QUEUE_SIZE)
        # interact with FileManager
        self._complete_pieces_to_write: trio.MemorySendChannel[
            CompletePieceToWrite | AllPiecesWritten
        ] = complete_pieces_to_write
        self._write_confirmations: trio.MemoryReceiveChannel[WriteConfirmation] = (
            write_confirmations
        )
        self._blocks_to_read: trio.MemorySendChannel[BlockToRead] = blocks_to_read
        self._blocks_for_peers: trio.MemoryReceiveChannel[Piece] = blocks_for_peers
        # interact with peer connections
        self._msg_from_peer: tuple[
            trio.MemorySendChannel[tuple[peer_state.PeerState, RawPeerMessage]],
            trio.MemoryReceiveChannel[tuple[peer_state.PeerState, RawPeerMessage]],
        ] = trio.open_memory_channel(config.INTERNAL_QUEUE_SIZE)
        # queues for sending TO peers are initialized on a per-peer basis
        self._peers: dict[bytes, peer_state.PeerState] = dict()
        # data received but not written to disk
        self._received_blocks: dict[int, tuple[bitarray.bitarray, bytearray]] = dict()
        self.requests = requests.RequestManager()
        self._stats: dict[StatField, int] = {f: 0 for f in StatField}

        if config.MAX_OUTGOING_BYTES_PER_SECOND is None:
            self.token_bucket: TokenBucket | None = None
        else:
            self.token_bucket = TokenBucket(config.MAX_OUTGOING_BYTES_PER_SECOND)

    def _inc_stats(self, field: StatField) -> None:
        self._stats[field] += 1
        logger.debug("stats updated: {}".format(self._stats))

    @property
    def peer_messages(self) -> trio.MemorySendChannel[tuple[peer_state.PeerState, RawPeerMessage]]:
        return self._msg_from_peer[0]

    async def run(self) -> None:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.control_loop)
            nursery.start_soon(self.peer_clients_loop)
            nursery.start_soon(self.peer_server_loop)
            nursery.start_soon(self.tracker_loop)
            nursery.start_soon(self.peer_messages_loop)
            nursery.start_soon(self.file_write_confirmation_loop)
            nursery.start_soon(self.file_reading_loop)
            nursery.start_soon(self.info_loop)
            nursery.start_soon(self.choking_loop)
            nursery.start_soon(
                self.delete_stale_requests_loop, config.DELETE_STALE_REQUESTS_SECONDS
            )
            if self.token_bucket is not None:
                nursery.start_soon(self.token_bucket.loop)

    async def control_loop(self) -> None:
        while True:
            complete_peers = [p.get_pieces().all for p in self._peers.values()]
            if (
                self._auto_shutdown and all(complete_peers) and self._state._complete.all()
            ):  # TODO remove private variable access
                await self._complete_pieces_to_write.send(AllPiecesWritten())
                raise KeyboardInterrupt  # TODO should use a better exception, or something else entirely
            elif self._state._complete.all():  # TODO remove private variable access
                await self._complete_pieces_to_write.send(AllPiecesWritten())
            await trio.sleep(2)

    async def info_loop(self) -> None:
        while True:
            num_unwritten_blocks = len(self._received_blocks.items())
            outstanding_requests = self.requests.size
            logger.info("stats = {}".format(self._stats))
            logger.info(
                "{} unwritten blocks, {} outstanding_requests, {}/{} complete pieces".format(
                    num_unwritten_blocks,
                    outstanding_requests,
                    sum(self._state._complete),
                    len(self._state._complete),
                )
            )
            # TODO 2026-03-01: Fixes were made to this if statement and logging, but as the
            # block is not triggered by the current integration tests it will need to be
            # verified at some point in the future.
            if (sum(self._state._complete) / len(self._state._complete) > 0.97) or (
                len(self._state._complete) - sum(self._state._complete) < 2
            ):
                logger.info("Outstanding requests = {}".format(self.requests._requests))
                unwritten_blocks = [
                    (i, b, len(data)) for i, (b, data) in self._received_blocks.items()
                ]
                logger.info("Unwritten blocks: {}".format(unwritten_blocks))
            channels: list[trio.MemorySendChannel[Any] | trio.MemoryReceiveChannel[Any]] = [
                self._peers_without_connection[0],
                self._complete_pieces_to_write,
                self._write_confirmations,
                self._blocks_to_read,
                self._blocks_for_peers,
                self._msg_from_peer[0],
            ]
            logger.info("Memory channels {}".format([c.statistics() for c in channels]))
            logger.info("Alive peers {}".format(self._peers.keys()))
            display.print_peers(self._state, self._peers)
            await trio.sleep(1)

    async def tracker_loop(self) -> None:
        new = True
        while True:
            logger.debug("tracker_loop")
            start_time = trio.current_time()
            event = b"started" if new else None
            raw_tracker_info = await tracker.query(self._state, event)
            tracker_info = bencode.parse_value(io.BytesIO(raw_tracker_info))
            if not isinstance(tracker_info, collections.OrderedDict):
                raise Exception(f"Invalid tracker info: {tracker_info!r}")
            # update peers
            # TODO we could recieve peers in a different format
            peer_ips_and_ports = bencode.parse_peers(tracker_info[b"peers"], self._state)
            peers = [
                (peer_state.PeerAddress(ip, port), peer_id)
                for ip, port, peer_id in peer_ips_and_ports
            ]
            logger.info("Found peers from tracker: {}".format(peers))
            await self.update_peers(peers)
            # update other info:
            # self._state.complete_peers = tracker_info['complete']
            # self._state.incomplete_peers = tracker_info['incomplete']
            # self._state.interval = int(tracker_info['interval'])
            # tell tracker the new interval
            await trio.sleep_until(start_time + self._state.interval)
            new = False

    async def peer_server_loop(self) -> None:
        await trio.serve_tcp(peer_connection.make_handler(self), self._state.listening_port)

    async def peer_clients_loop(self) -> None:
        """
        Start up clients for new peers that are not from the serve.
        """
        logger.debug("starting peer_clients_loop")
        async with trio.open_nursery() as nursery:
            while True:
                logger.debug("peer_clients_loop")
                address = await self._peers_without_connection[1].receive()
                nursery.start_soon(peer_connection.make_standalone, self, address)

    async def update_peers(self, peers: list[tuple[peer_state.PeerAddress, bytes | None]]) -> None:
        for address, peer_id in peers:
            if peer_id in self._peers:
                logger.info("Peer already exists: {!r}".format(peer_id))
            else:
                logger.info("Adding new peer to queue: {!r} / {!r}".format(address, peer_id))
                await self._peers_without_connection[0].send(address)

    def _blocks_from_index(self, index: int) -> set[Block]:
        piece_length = self._state.piece_length(index)
        block_length = min(piece_length, config.BLOCK_SIZE)
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
        if self._state._complete.all():
            logger.info("Not making new requests, download is complete")
            return
        if not self._peers:
            logger.info("Not making new requests as there are no peers")
            return
        for address, peer in self._peers.items():
            if peer.is_client_choked:
                continue
            # TODO don't read private field of another object
            targets = (~self._state._complete) & peer._pieces
            target_index = _pick_random_one_in_bitarray(targets)
            if target_index is not None:
                logger.info(
                    "{!r}: self any? {}, peer any? {}, target_index = {}".format(
                        address, self._state._complete.any(), peer._pieces.any(), target_index
                    )
                )
                existing_requests = self.requests.existing_requests_for_peer(address)
                if len(existing_requests) > config.MAX_OUTSTANDING_REQUESTS_PER_PEER:
                    logger.info(
                        "{!r}: Not making new requests: {} existing".format(
                            address, len(existing_requests)
                        )
                    )
                    new_requests: set[Block] = set()
                else:
                    suggested_requests = self._blocks_from_index(target_index)
                    new_requests = suggested_requests.difference(existing_requests)
                    logger.info(
                        "{!r}: {} suggested requests, {} existing".format(
                            address, len(suggested_requests), len(existing_requests)
                        )
                    )
                logger.info("{!r}: new_requests = {}".format(address, new_requests))
                if new_requests:
                    for r in new_requests:
                        self.requests.add_request(address, r)
                        self._inc_stats(StatField.REQUESTS_OUT)
                    await peer.send_outgoing_data.send(Request(blocks=new_requests))
            else:
                logger.info("No target pieces for {!r}".format(address))

    async def handle_peer_message(self, peer_id: bytes, raw_msg: RawPeerMessage) -> None:
        if peer_id not in self._peers:
            logger.info("did not handle message because peer {!r} no longer exists".format(peer_id))
            return
        peer_state = self._peers[peer_id]
        match raw_msg.msg_type:
            case peer_messages.MessageTypeByte.CHOKE:
                logger.info("Received CHOKE from {!r}".format(peer_id))
                peer_state.choke_us()
            case peer_messages.MessageTypeByte.UNCHOKE:
                logger.info("Received UNCHOKE from {!r}".format(peer_id))
                peer_state.unchoke_us()
            case peer_messages.MessageTypeByte.INTERESTED:
                logger.warning(
                    "Received INTERESTED from {!r} (not implemented)".format(peer_id)
                )  # TODO
            case peer_messages.MessageTypeByte.NOT_INTERESTED:
                logger.warning(
                    "Received NOT_INTERESTED from {!r} (not implemented)".format(peer_id)
                )  # TODO
            case peer_messages.MessageTypeByte.HAVE:
                index: int = peer_messages.parse_have(raw_msg.payload)
                logger.debug("Received HAVE {} from {!r}".format(index, peer_id))
                peer_state.get_pieces()[index] = True
            case peer_messages.MessageTypeByte.BITFIELD:
                logger.info("Received BITFIELD from {!r}".format(peer_id))
                # TODO would be useful to log what percentage of the file the peer has
                bitfield = peer_messages.parse_bitfield(raw_msg.payload)
                peer_state.set_pieces(bitfield)
            case peer_messages.MessageTypeByte.REQUEST:
                self._inc_stats(StatField.REQUESTS_IN)
                request_info = peer_messages.parse_request_or_cancel(raw_msg.payload)
                logger.info(
                    "Received REQUEST from {} from {}".format(request_info, peer_state.peer_id)
                )
                if peer_state.is_peer_choked:
                    logger.warning(
                        "{} requested {} but peer is choked".format(
                            peer_state.peer_id, request_info.piece_index
                        )
                    )
                elif self._state._complete[request_info.piece_index]:
                    await self._blocks_to_read.send(
                        BlockToRead(peer_id=peer_state.peer_id, block=request_info)
                    )
                else:
                    logger.warning(
                        "{} requested {} but piece is incomplete".format(
                            peer_state.peer_id, request_info.piece_index
                        )
                    )
            case peer_messages.MessageTypeByte.PIECE:
                (index, begin, data) = peer_messages.parse_piece(raw_msg.payload)
                self._inc_stats(StatField.BLOCKS_IN)
                logger.info(
                    "Received block {} from {}".format(
                        (index, begin, len(data)), peer_state.peer_id
                    )
                )
                peer_state.inc_download_counters()
                await self.handle_block_received(index, begin, data)
            case peer_messages.MessageTypeByte.CANCEL:
                logger.warning(
                    "Received CANCEL from {!r} (not implemented)".format(peer_id)
                )  # TODO
                request_info = peer_messages.parse_request_or_cancel(raw_msg.payload)
            case _:
                # TODO - Exceptions are bad here! Should this be assert false?
                error_message = "Bad message: msg_type = {}, msg_payload = {!r}".format(
                    raw_msg.msg_type, raw_msg.payload
                )
                logger.error(error_message)
                raise Exception(error_message)

    async def handle_block_received(self, index: int, begin: int, data: bytes) -> None:
        if index not in self._received_blocks:
            piece_length = self._state.piece_length(index)
            completed_blocks = bitarray.bitarray(math.ceil(piece_length / config.BLOCK_SIZE))
            completed_blocks.setall(False)
            piece_data = bytearray(piece_length)
            self._received_blocks[index] = (completed_blocks, piece_data)
        else:
            completed_blocks = self._received_blocks[index][0]
            piece_data = self._received_blocks[index][1]
        block_index = begin // config.BLOCK_SIZE
        completed_blocks[block_index] = True
        piece_data[begin : begin + len(data)] = data
        if completed_blocks.all():
            piece_info = self._state.piece_info(index)
            complete_piece = bytes(piece_data)
            if hashlib.sha1(complete_piece).digest() == piece_info.sha1hash:
                self._received_blocks.pop(index)  # TODO is this ordering significant?
                await self._complete_pieces_to_write.send(
                    CompletePieceToWrite(index=index, data=complete_piece)
                )
            else:
                self._received_blocks.pop(index)
                self.requests.delete_all_for_piece(index)
                logger.warning("sha1hash does not match for index {}".format(index))

    async def peer_messages_loop(self) -> None:
        while True:
            logger.debug("peer_messages_loop")
            peer_state, raw_msg = await self._msg_from_peer[1].receive()  # TODO should use peer_id
            logger.debug("Engine recieved peer message from {}".format(peer_state.peer_id))
            await self.handle_peer_message(peer_state.peer_id, raw_msg)  # TODO should use peer_id
            await self.update_peer_requests()

    async def announce_have_piece(self, index: int) -> None:
        peers = (
            self._peers.copy()
        )  # shallow copy, but that should be enough as we're not modifying the PeerState objects
        for _peer_id, peer_s in peers.items():
            await peer_s.send_outgoing_data.send(Have(piece_index=index))

    async def file_write_confirmation_loop(self) -> None:
        while True:
            logger.debug("file_write_confirmation_loop")
            confirmation = await self._write_confirmations.receive()
            self.requests.delete_all_for_piece(confirmation.index)
            # NB - update the _complete vector first to guarantee that new clients get
            # the most upto date bitfield (they may also get a redundant HAVE message)
            self._state._complete[confirmation.index] = True  # TODO remove private property access
            await self.announce_have_piece(confirmation.index)
            await self.update_peer_requests()

    async def file_reading_loop(self) -> None:
        while True:
            logger.debug("file_reading_loop")
            msg = await self._blocks_for_peers.receive()
            self._inc_stats(StatField.BLOCKS_OUT)
            if msg.peer_id in self._peers:
                p_state = self._peers[msg.peer_id]
                p_state.inc_upload_counters()
                await p_state.send_outgoing_data.send(msg)
            else:
                logger.info(
                    "dropped block {} for {!r} because peer no longer exists".format(
                        msg.block, msg.peer_id
                    )
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
            logger.info(
                "Peers ordered by successful downloads in last 20 seconds: {}".format(peers)
            )
            # First X are unchoked
            # Rest are choked
            unchoke = set(p[0] for p in peers[: config.NUM_UNCHOKED_PEERS])
            choke = set(p[0] for p in peers[config.NUM_UNCHOKED_PEERS :])
            if optimistic_unchoke:
                unchoke.add(optimistic_unchoke)
                choke.discard(optimistic_unchoke)
            for p_id in unchoke:
                if p_id in self._peers:  # protect against state change while putting in queue
                    p_state = self._peers[p_id]
                    alert = p_state.unchoke_them()
                    p_state.reset_rolling_download_count()
                    if alert == peer_state.ChokeAlert.ALERT:
                        await p_state.send_outgoing_data.send(Unchoke())
            for p_id in choke:
                if p_id in self._peers:  # protect against state change while putting in queue
                    p_state = self._peers[p_id]
                    alert = p_state.choke_them()
                    p_state.reset_rolling_download_count()
                    if alert == peer_state.ChokeAlert.ALERT:
                        await p_state.send_outgoing_data.send(Choke())
            # update period
            period = (period + 1) % 3  # rotate period every 30 seconds

    async def delete_stale_requests_loop(self, seconds: int) -> None:
        while True:
            await trio.sleep(seconds)
            count = self.requests.delete_older_than(seconds=seconds)
            logging.info("Deleted {} stale requests (older than {} seconds)".format(count, seconds))


def run(torrent):
    try:
        # create FileManager and check hashes if file already exists
        file_wrapper = file_manager.FileWrapper(torrent=torrent)
        existing_hashes = file_wrapper.create_file_or_return_hashes()

        if existing_hashes:
            for index, h in enumerate(existing_hashes):
                piece_info = torrent.piece_info(index)
                if piece_info.sha1hash == h:
                    torrent._complete[index] = True  # TODO remove private property access

        s_complete_pieces, r_complete_pieces = trio.open_memory_channel[
            CompletePieceToWrite | AllPiecesWritten
        ](config.INTERNAL_QUEUE_SIZE)
        s_write_confirmations, r_write_confirmations = trio.open_memory_channel[WriteConfirmation](
            config.INTERNAL_QUEUE_SIZE
        )
        s_blocks_to_read, r_blocks_to_read = trio.open_memory_channel[BlockToRead](
            config.INTERNAL_QUEUE_SIZE
        )
        s_blocks_for_peers, r_blocks_for_peers = trio.open_memory_channel[Piece](
            config.INTERNAL_QUEUE_SIZE
        )

        file_engine = file_manager.FileManager(
            file_wrapper=file_wrapper,
            pieces_to_write=r_complete_pieces,
            write_confirmations=s_write_confirmations,
            blocks_to_read=r_blocks_to_read,
            blocks_for_peers=s_blocks_for_peers,
        )

        eng = Engine(
            torrent=torrent,
            complete_pieces_to_write=s_complete_pieces,
            write_confirmations=r_write_confirmations,
            blocks_to_read=s_blocks_to_read,
            blocks_for_peers=r_blocks_for_peers,
        )

        async def run():
            async with trio.open_nursery() as nursery:
                nursery.start_soon(file_engine.run)
                nursery.start_soon(eng.run)

        trio.run(run)
    except KeyboardInterrupt:
        print()
        print("Shutting down without cleanup...")
