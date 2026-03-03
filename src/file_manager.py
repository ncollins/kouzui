import hashlib
import logging
import os
from typing import Any

import trio

import torrent as tstate
from internal_messages import (
    AllPiecesWritten,
    BlockToRead,
    CompletePieceToWrite,
    WriteConfirmation,
)
from peer_messages import Piece

logger = logging.getLogger("file_manager")


def _create_empty_file(path, torrent):
    with open(path, "wb") as f:
        for i in range(torrent._num_pieces):  # TODO remove private property access
            b = bytes(torrent.piece_length(i))
            f.write(b)


class FileWrapper(object):
    def __init__(self, *, torrent: tstate.Torrent, file_suffix: str = "") -> None:
        self._torrent = torrent
        self._tmp_path = torrent.file_path + file_suffix + ".part"
        self._final_path = torrent.file_path + file_suffix
        self._file_path = None
        self._file: Any = None

    def create_file_or_return_hashes(self):
        if os.path.exists(self._final_path):
            self._file_path = self._final_path
            logger.info(f"data file exists at {self._file_path}")
        else:
            self._file_path = self._tmp_path
            logger.info(f"using _tmp_path at {self._tmp_path}")

        assert self._file_path is not None

        try:
            self._file = open(self._file_path, "rb")
            hashes = []
            for i, _ in enumerate(self._torrent._complete):
                piece_length = self._torrent.piece_length(i)
                p = self.read_block(i, 0, piece_length)
                h = hashlib.sha1(p).digest()
                hashes.append(h)
            self._file.close()
            logger.info("found file and calculated existing hashes")
        except FileNotFoundError:
            _create_empty_file(self._file_path, self._torrent)  # TODO don't read private property
            logger.info(f"created empty file at {self._file_path}")
            hashes = None
        self._file = open(self._file_path, "rb+")
        return hashes

    def write_piece(self, index: int, piece: bytes) -> None:
        start = index * self._torrent._piece_length  # TODO
        self._file.seek(start)
        self._file.write(piece)
        self._file.flush()

    def read_block(self, index: int, begin: int, length: int) -> bytes:
        start = index * self._torrent._piece_length + begin
        self._file.seek(start)
        block = self._file.read(length)
        return block

    def move_file_to_final_location(self):
        assert self._file_path is not None
        if self._file_path != self._final_path:
            self._file.close()
            os.rename(self._file_path, self._final_path)
            logger.info(f"Moved {self._file_path} to {self._final_path}")
            self._file_path = self._final_path
            self._file = open(self._file_path, "rb+")


class FileManager(object):
    def __init__(
        self,
        *,
        file_wrapper: FileWrapper,
        pieces_to_write: trio.MemoryReceiveChannel[CompletePieceToWrite | AllPiecesWritten],
        write_confirmations: trio.MemorySendChannel[WriteConfirmation],
        blocks_to_read: trio.MemoryReceiveChannel[BlockToRead],
        blocks_for_peers: trio.MemorySendChannel[Piece],
    ) -> None:
        self._file_wrapper = file_wrapper
        self._pieces_to_write = pieces_to_write
        self._write_confirmations = write_confirmations
        self._blocks_to_read = blocks_to_read
        self._blocks_for_peers = blocks_for_peers

    # async def move_file_to_final_location(self):
    #    self._file_wrapper.move_file_to_final_location()

    async def run(self):
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.piece_writing_loop)
            nursery.start_soon(self.block_reading_loop)

    async def piece_writing_loop(self):
        while True:
            msg = await self._pieces_to_write.receive()
            if isinstance(msg, AllPiecesWritten):
                self._file_wrapper.move_file_to_final_location()
            else:
                self._file_wrapper.write_piece(msg.index, msg.data)
                logger.info(f"Wrote #{msg.index} to disk")
                await self._write_confirmations.send(WriteConfirmation(index=msg.index))

    async def block_reading_loop(self):
        while True:
            msg = await self._blocks_to_read.receive()
            data = self._file_wrapper.read_block(
                msg.block.piece_index, msg.block.block_start, msg.block.block_length
            )
            await self._blocks_for_peers.send(
                Piece(peer_id=msg.peer_id, block=msg.block, data=data)
            )
