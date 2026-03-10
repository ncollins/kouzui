import hashlib
import io
import logging
from pathlib import Path

import trio

import torrent as tstate
from internal_messages import (
    AllPiecesWritten,
    BlockToRead,
    CompletePieceToWrite,
    WriteConfirmation,
)
from peer_messages import Piece
from shared_types import PeerId

logger = logging.getLogger("file_manager")


def _create_empty_file(path: Path, torrent: tstate.Torrent) -> None:
    with path.open("wb") as f:
        for i in range(torrent._num_pieces):  # TODO remove private property access
            b = bytes(torrent.piece_length(i))
            f.write(b)


class FileWrapper(object):
    def __init__(self, *, torrent: tstate.Torrent, file_suffix: str = "") -> None:
        self._torrent = torrent
        self._tmp_path = torrent.file_path.parent / f"{torrent.file_path.name}{file_suffix}.part"
        self._final_path = torrent.file_path.parent / f"{torrent.file_path.name}{file_suffix}"
        self._file_path: Path | None = None
        self._file_handle: io.BufferedReader | io.BufferedRandom | None = None

    def create_file_or_return_hashes(self) -> list[bytes] | None:
        if self._final_path.exists():
            self._file_path = self._final_path
            logger.info(f"data file exists at {self._file_path}")
        else:
            self._file_path = self._tmp_path
            logger.info(f"using _tmp_path at {self._tmp_path}")

        assert self._file_path is not None

        try:
            self._file_handle = self._file_path.open("rb")
            hashes = []
            for i, _ in enumerate(self._torrent._complete):
                piece_length = self._torrent.piece_length(i)
                p = self.read_block(i, 0, piece_length)
                h = hashlib.sha1(p).digest()
                hashes.append(h)
            self._file_handle.close()
            logger.info("found file and calculated existing hashes")
        except FileNotFoundError:
            _create_empty_file(self._file_path, self._torrent)  # TODO don't read private property
            logger.info(f"created empty file at {self._file_path}")
            hashes = None
        self._file_handle = open(self._file_path, "rb+")
        return hashes

    def write_piece(self, index: int, piece: bytes) -> None:
        start = index * self._torrent._piece_length  # TODO
        assert self._file_handle is not None
        self._file_handle.seek(start)
        self._file_handle.write(piece)
        self._file_handle.flush()

    def read_block(self, index: int, begin: int, length: int) -> bytes:
        start = index * self._torrent._piece_length + begin
        assert self._file_handle is not None
        self._file_handle.seek(start)
        block = self._file_handle.read(length)
        return block

    def move_file_to_final_location(self) -> None:
        assert self._file_path is not None
        assert self._file_handle is not None
        if self._file_path != self._final_path:
            self._file_handle.close()
            self._file_path.rename(self._final_path)
            logger.info(f"Moved {self._file_path} to {self._final_path}")
            self._file_path = self._final_path
            self._file_handle = self._file_path.open("rb+")


async def file_manager_loop(
    *,
    file_wrapper: FileWrapper,
    receive_from_engine: trio.MemoryReceiveChannel[
        CompletePieceToWrite | AllPiecesWritten | BlockToRead
    ],
    send_to_engine: trio.MemorySendChannel[WriteConfirmation | tuple[PeerId, Piece]],
) -> None:
    while True:
        msg = await receive_from_engine.receive()
        match msg:
            case AllPiecesWritten():
                file_wrapper.move_file_to_final_location()
            case CompletePieceToWrite(index=index, data=data):
                file_wrapper.write_piece(index, data)
                logger.info(f"Wrote #{index} to disk")
                await send_to_engine.send(WriteConfirmation(index=index))
            case BlockToRead(peer_id=peer_id, block=block):
                data = file_wrapper.read_block(
                    block.piece_index, block.block_start, block.block_length
                )
                await send_to_engine.send(
                    (
                        peer_id,
                        Piece(
                            piece_index=block.piece_index,
                            block_start=block.block_start,
                            data=data,
                        ),
                    )
                )
