import trio

import config

def _int2bytes(i):
    return b'%d' % i

def tracker_request(torrent):
    """
    Tracker request is an http GET request, sent with parameters telling
    the tracker about your client.
    """
    d = { 
        b'info_hash': torrent.info_hash,
        b'peer_id': torrent.peer_id,
        # ip
        b'port': _int2bytes(config.listening_port),
        b'uploaded': _int2bytes(torrent.uploaded),
        b'downloaded': _int2bytes(torrent.downloaded),
        b'left': _int2bytes(torrent.left),
        # event
    }
    params = b'&'.join([k + b'=' + v for k, v in d.items()])
    path = b'/' + torrent.tracker_path + b'?' + params

    request_lines = [
        [ b'GET', path ],
        [ b'Host:', torrent.tracker_url]
    ]
    request = b'\n'.join(k + b' ' + v for k,v in request_lines)
    return request

async def query_tracker(torrent):
    stream = await trio.open_tcp_stream('localhost',8181)
    await stream.send_all(tracker_request(torrent))
    finished = False
    while not finished:
        reply = await stream.receive_some(1000)
        finished = True
    return reply


async def download_torrent(torrent):
    tracker_info = await query_tracker(torrent)
    print(tracker_info)

def run(torrent):
    trio.run(download_torrent, torrent)
