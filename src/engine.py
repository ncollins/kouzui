import io

import trio

import bencode
import tracker

async def main_loop(torrent):
    raw_tracker_info = await tracker.query(torrent)
    tracker_info = bencode.parse_value(io.BytesIO(raw_tracker_info))
    peers = bencode.parse_compact_peers(tracker_info[b'peers']) 
    print(tracker_info)
    print(peers)

def run(torrent):
    trio.run(main_loop, torrent)
