import h11
import trio

import config
import http_stream

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
    r = h11.Request(method="GET", target=path, headers=[("Host", torrent.tracker_url)])
    return r

async def query_tracker(torrent):
    stream = await trio.open_tcp_stream('localhost',8181)
    h = http_stream.Http_stream(stream, h11.CLIENT)

    await h.send_event(tracker_request(torrent))
    await h.send_event(h11.EndOfMessage())

    response, data = await h.receive_with_data()
    return data


async def download_torrent(torrent):
    tracker_info = await query_tracker(torrent)
    print(tracker_info)

def run(torrent):
    trio.run(download_torrent, torrent)
