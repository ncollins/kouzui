import io
import logging
from urllib import parse as urllib_parse # hack to fix mypy warning

import h11
import trio

import bencode
import http_stream

from config import LISTENING_PORT

logger = logging.getLogger('tracker')

def _int2bytes(i : int) -> bytes:
    return b'%d' % i

def tracker_request(torrent, event) -> h11.Request:
    """
    Tracker request is an http GET request, sent with parameters telling
    the tracker about your client.
    """
    d = { 
        b'info_hash': urllib_parse.quote_from_bytes(torrent.info_hash).encode()
        , b'peer_id': torrent.peer_id
        # ip
        , b'port': _int2bytes(LISTENING_PORT)
        , b'uploaded': _int2bytes(torrent.uploaded)
        , b'downloaded': _int2bytes(torrent.downloaded)
        , b'left': _int2bytes(torrent.left)
        #, b'event': event
        #, b'compact': b'1'
        , b'compact': b'0'
        # testing
        #, b'supportcrypto': b'1'
        #, b'key': b'71c04610'
        #, b'numwant': b'80'
    }
    if event:
        d[b'event'] = event
    params = b'&'.join([k + b'=' + v for k, v in d.items()])
    path = torrent.tracker_path + b'?' + params
    host = torrent.tracker_address + b':' + str(torrent.tracker_port).encode()
    headers = [
            ("Host", host)
            , ("Accept-Encoding", "gzip;q=1.0, deflate, identity")
            , ("Accept", "*/*")
            , ("User-Agent", "toytorrent")
            ]
    r = h11.Request(method="GET", target=path, headers=headers)
    return r

async def query(torrent, event) -> bytes:
    url: bytes = torrent.tracker_address
    port: bytes = torrent.tracker_port
    logger.debug('url/port = {}/{}'.format(url, port))
    stream = await trio.open_tcp_stream(url.decode("ascii"), port) # TODO fix hack with string/bytes issue
    logger.debug('Opened raw stream')
    h = http_stream.Http_stream(stream, h11.CLIENT)
    logger.debug('Created Http_stream')

    await h.send_event(tracker_request(torrent, event))
    await h.send_event(h11.EndOfMessage())

    response, data = await h.receive_with_data()
    return b''.join(d.data for d in data)
