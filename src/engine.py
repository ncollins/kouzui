import trio

import tracker

async def main_loop(torrent):
    await tracker.download_torrent(torrent)

def run(torrent):
    trio.run(main_loop, torrent)
