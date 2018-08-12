import trio

import torrent as tstate

class FileManager(object):
    def __init__(self, torrent: tstate.Torrent):
        self._torrent = torrent
        self._raw_file = open(self._torrent.file_path, 'rb+')
        self._file = trio.wrap_file(self._raw_file)

    async def write_piece(self, index: int, piece: bytes) -> None:
        start = index * self._torrent.piece_length
        await self._file.seek(start)
        await self._file.write(piece)
        await self._file.flush()

    async def read_block(self, index: int, begin: int, length: int) -> bytes:
        start = index * self._torrent.piece_length + begin
        await self._file.seek(start)
        return await self._file.read(length)
