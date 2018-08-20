#import collections
import datetime
import hashlib
import itertools
import os
import random
import re
from typing import NamedTuple, Any, List, Dict, Tuple, Optional, Set

import bitarray
import trio

from config import LISTENING_PORT

# Key information in torrent dictionary, d:
#
# d['announce'] -> the url of the tracker
#
# d['info']['name'] -> suggested file or directory name
#
# d['info']['pieces'] -> string with length that's a multiple of 20, each 20 byte
# section is the SHA1 hash of of the entry at that index
#
# d['info']['piece length'] -> number of bytes of each piece, with the
# exception of the last one (may be shorter)
#
# d['info']['length'] -> if single file, the length in bytes
# OR 
# d['info']['files'] -> if multiple files, a list of dictionaries with 
# 'length' and 'path' keys


PeerAddress = NamedTuple('PeerAddress', [('ip', bytes), ('port', int)])


class PeerState(object):
    def __init__(self, pieces: bitarray.bitarray, first_seen: datetime.datetime, peer_id = None) -> None:
        self._pieces = pieces
        self._first_seen = first_seen
        self._last_seen = first_seen
        self._peer_id = peer_id
        self._requested: Set[Tuple[int,int,int]] = set()
        self._to_send_queue = trio.Queue(100) # TODO remove magic number

    def add_request(self, request_info: Tuple[int,int,int]) -> None:
        self._requested.add(request_info)

    def cancel_request(self, request_info: Tuple[int,int,int]) -> None:
        self._requested.discard(request_info)

    def get_pieces(self):
        return self._pieces

    def set_pieces(self, new_pieces):
        # crop the new pieces because peers send data
        # in complete bytes
        length = len(self._pieces)
        self._pieces = new_pieces[:length]

    @property
    def first_seen(self):
        return self._first_seen

    @property
    def last_seen(self):
        return self._last_seen

    @property
    def peer_id(self):
        return self._peer_id

    def set_peer_id(self, peer_id):
        self._peer_id = peer_id

    @property
    def to_send_queue(self) -> trio.Queue:
        return self._to_send_queue

Piece = NamedTuple('Piece', [ ('filename', str), ('index', int), ('sha1hash', bytes)]) 

def _random_char() -> str:
    # ASCII ranges
    # 65-90: A-Z
    # 97-122: a-z
    # 48-57: 0-9
    n = random.randint(0, 61)
    if n < 26:
        c = chr(n + 65)
    elif n < 52:
        c = chr(n - 26 + 97)
    else:
        c = chr(n - 52 + 48)
    return c

def _generate_peer_id() -> bytes:
    return ''.join(_random_char() for _ in range(0,20)).encode()


def _parse_pieces(bstring: bytes) -> List[bytes]:
    if (len(bstring) % 20) != 0:
        raise Exception("'pieces' is not a multiple of 20'")
    else:
        l: List[bytes] = []
        i = 0
        while i + 20 < len(bstring):
            l.append(bstring[i:i+20])
            i += 20
        return l


class Torrent(object):
    """
    The Torrent object stores all information about an active torrent.
    It is initiallised with the dictionary values taken from the
    .torrent file. It then takes data from the tracker and peers.

    None of the methods are async, but it can trigger async events by pushing
    messages into a trio.Queue, which can be used in a blocking or non-blocking
    fashion.
    """
    def __init__(self, tdict, info_string, directory, listening_port=None, custom_name=None):
        self._listening_port = listening_port
        self._info_string = info_string
        #print(info_string)
        self._info_hash = hashlib.sha1(info_string).digest()
        self._peer_id = _generate_peer_id()
        self._uploaded = 0
        self._downloaded = 0
        self._piece_length = int(tdict[b'info'][b'piece length'])
        if b'files' in tdict[b'info']: # multi-file case
            raise Exception("multi-file torrents not yet supported")
        else: # single file case
            # store hash and a bolean to mark if we have the piece or not
            self._torrent_name = bytes.decode(tdict[b'info'][b'name'])
            if custom_name:
                self._filename = os.path.join(directory, custom_name)
            else:
                self._filename = os.path.join(directory, self._torrent_name)

            self._pieces = [
                    Piece(self._filename, i, sha1) 
                    for i, sha1 
                    in enumerate(_parse_pieces(tdict[b'info'][b'pieces']))
                    ]

            self._file_length = int(tdict[b'info'][b'length'])
            self._left = self._file_length

            self._num_pieces = len(self._pieces)
            self._complete = bitarray.bitarray(self._num_pieces)
            self._complete.setall(False)

        # deconstruct url
        self._raw_tracker_url = tdict[b'announce']
        r = re.compile(r'(?P<http>http://)?(?P<address>.+):(?P<port>\d+)(?P<path>.+)')
        m = r.fullmatch(self._raw_tracker_url.decode())
        g = m.groupdict()
        self._tracker_address = m['address'].encode()
        self._tracker_port = int(m['port'])
        self._tracker_path = m['path'].encode()
        print('Tracker address: {}, port: {}, path: {}'.format(self._tracker_address, self._tracker_port, self._tracker_path))

        # info not from .torrent file
        self._peers: Dict[PeerAddress, PeerState] = {}
        self._interval = 100
        self._complete_peers = 0
        self._incomplete_peers = 0
        # data received but not written to disk
        self._received_blocks: Dict[int, List[Tuple[int,bytes]]] = {}
        # ------------------------------------------
        # Queues are used for outbound communication
        # ------------------------------------------
        #self._complete_pieces_queue: trio.Queue[Tuple[int,bytes]] = trio.Queue()
        #self._incoming_request_queue: trio.Queue[Tuple[int,int,int]] = trio.Queue()
        #self._requests_from_peers_for_file_blocks: trio.Queue[Tuple[int,int,int]] = trio.Queue()

    @property
    def listening_port(self):
        if self._listening_port:
            return self._listening_port
        else:
            return LISTENING_PORT

    @property
    def file_path(self):
        return self._filename

    @property
    def incoming_request_queue(self):
        return self._incoming_request_queue

    @property
    def complete_pieces_queue(self):
        return self._complete_pieces_queue

    @property
    def piece_length(self):
        return self._piece_length

    @property
    def info_hash(self):
        return self._info_hash

    @property
    def peer_id(self):
        return self._peer_id

    @property
    def interval(self):
        return self._interval

    @property
    def tracker_address(self) -> bytes:
        # TODO - this is very lazy!
        #without_path = self._raw_tracker_url.rsplit(b'/', 1)[0]
        #without_port = without_path.rsplit(b':', 1)[0]
        #without_protocol = without_port.replace(b'http://',b'')
        #return without_protocol
        return self._tracker_address

    @property
    def tracker_port(self) -> int:
        #print('tracker_url = {}'.format(self.tracker_url))
        #return int(self._raw_tracker_url.rsplit(b':', 1)[1])
        return self._tracker_port


    @property
    def tracker_path(self):
        # TODO - this is very lazy!
        #return self._raw_tracker_url.rsplit(b'/', 1)[1]
        return self._tracker_path

    @property
    def uploaded(self):
        # TODO this needs to update while we run
        return 0

    @property
    def downloaded(self):
        # TODO this needs to update while we run
        return 0

    @property
    def left(self):
        # TODO this needs to update while we run
        return self._file_length

    def piece_info(self, n):
        return self._pieces[n]

    def is_piece_complete(self, index):
        return self._complete[index]

    def create_peer_state(self, peer: PeerAddress) -> PeerState:
        pieces = bitarray.bitarray(self._num_pieces)
        pieces.setall(False)
        # TODO this is crappy as peers collected from tracker at same time
        # will have different last_seen
        now = datetime.datetime.now()
        peer_state = PeerState(pieces, now)
        self._peers[peer] = peer_state
        return peer_state

    def get_or_add_peer(self, peer: PeerAddress) -> PeerState:
        if peer in self._peers:
            return self._peers[peer]
        else:
            return self.create_peer_state(peer)

    def pieces_to_request(self, peer: PeerAddress, n=10) -> List[int]:
        peer_state = self._peers[peer]
        targets = (~self._complete) & peer_state._pieces
        indexes = [i for i, b in enumerate(targets) if b]
        random.shuffle(indexes)
        return indexes[:n]

    #def add_data(self, index: int, begin: int, data: bytes) -> None:
    #    if index not in self._received_blocks:
    #        self._received_blocks[index] = []
    #    blocks = self._received_blocks[index]
    #    blocks.append((begin, data))
    #    piece_data = b''
    #    for offset, block_data in blocks:
    #        if offset == len(piece_data):
    #            piece_data = piece_data + block_data
    #        else:
    #            break
    #    piece_info = self._pieces[index]
    #    if len(piece_data) == self._piece_length:
    #        if hashlib.sha1(piece_data).digest() == piece_info.sha1:
    #            self._complete_pieces_queue.put_nowait((index, piece_data))
    #            self._received_blocks.pop(index)
    #        else:
    #            raise Exception('sha1hash does not match for index {}'.format(index))
