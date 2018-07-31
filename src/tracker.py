import io

import h11
import trio

import bencode
import http_stream

from config import LISTENING_PORT

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
        b'port': _int2bytes(LISTENING_PORT),
        b'uploaded': _int2bytes(torrent.uploaded),
        b'downloaded': _int2bytes(torrent.downloaded),
        b'left': _int2bytes(torrent.left),
        b'event': b'started',
        b'compact': b'1'
    }
    params = b'&'.join([k + b'=' + v for k, v in d.items()])
    path = b'/' + torrent.tracker_path + b'?' + params
    r = h11.Request(method="GET", target=path, headers=[("Host", torrent.tracker_url)])
    return r

async def query(torrent):
    #tracker_url = b'localhost:8181'
    tracker_url = torrent.tracker_url
    print(tracker_url)
    url = tracker_url.rsplit(b':', 1)[0].replace(b'http://',b'')
    port = int(tracker_url.rsplit(b':', 1)[1])
    print('url = {}'.format(str(url)))
    print('port = {}'.format(str(port)))
    stream = await trio.open_tcp_stream(url, port)
    print('Opened raw stream')
    h = http_stream.Http_stream(stream, h11.CLIENT)
    print('Created Http_stream')

    await h.send_event(tracker_request(torrent))
    await h.send_event(h11.EndOfMessage())

    response, data = await h.receive_with_data()
    return b''.join(d.data for d in data)
