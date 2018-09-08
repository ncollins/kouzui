import hashlib
import logging

logger = logging.getLogger('file_manager')

import trio

import torrent as tstate

def _create_empty_file(path, size_in_bytes):
    with open(path, 'wb') as f:
        for i in range(size_in_bytes):
            f.write(b'\x00')

class FileManager(object):
    def __init__(self, torrent: tstate.Torrent, pieces_to_write: trio.Queue, write_confirmations: trio.Queue, blocks_to_read: trio.Queue, blocks_for_peers: trio.Queue) -> None:
        self._torrent = torrent
        self._pieces_to_write = pieces_to_write
        self._write_confirmations = write_confirmations
        self._blocks_to_read = blocks_to_read
        self._blocks_for_peers = blocks_for_peers
        self._file = None

    def create_file_or_return_hashes(self):
        try:
            self._file = open(self._torrent.file_path, 'rb')
            hashes = []
            for i, _ in enumerate(self._torrent._complete):
                l = self._torrent.piece_length(i)
                p = self.read_block(i, 0, l)
                h = hashlib.sha1(p).digest()
                hashes.append(h)
            self._file.close()
        except FileNotFoundError:
            _create_empty_file(self._torrent.file_path, self._torrent._file_length) # TODO don't read private property
            hashes = None
        self._file = open(self._torrent.file_path, 'rb+')
        return hashes

    async def run(self):
        if self._file is None:
            raise Exception('FileManger must be initialised by calling `create_file_or_return_hashes` before `run`')
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self.piece_writing_loop)
            nursery.start_soon(self.block_reading_loop)

    async def piece_writing_loop(self):
        while True:
            index, piece = await self._pieces_to_write.get()
            self.write_piece(index, piece)
            logger.info('Wrote #{} to disk'.format(index))
            await self._write_confirmations.put(index)
        
    async def block_reading_loop(self):
        while True:
            who, (index, begin, length) = await self._blocks_to_read.get()
            logger.info('Received read request: {} for {}'.format((index, begin, length), who))
            block = self.read_block(index, begin, length)
            logger.info('Sending block back: {} for {}'.format((index, begin, length), who))
            await self._blocks_for_peers.put((who, (index, begin, length), block))

    def write_piece(self, index: int, piece: bytes) -> None:
        start = index * self._torrent._piece_length # TODO
        self._file.seek(start)
        self._file.write(piece)
        self._file.flush()

    def read_block(self, index: int, begin: int, length: int) -> bytes:
        start = index * self._torrent._piece_length + begin
        self._file.seek(start)
        block = self._file.read(length)
        logging.info('Read sha1 {}: {}'.format((index,begin), hashlib.sha1(block).digest()))
        return block
