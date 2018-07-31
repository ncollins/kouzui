import hashlib
import os
import random
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

def _random_char():
    # ASCII ranges
    # 65-90 : A-Z
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

def _generate_peer_id():
    return ''.join(_random_char() for _ in range(0,20)).encode()


def _parse_pieces(bstring):
    if (len(bstring) % 20) != 0:
        raise Exception("'pieces' is not a multiple of 20'")
    else:
        l = []
        i = 0
        while i + 20 < len(bstring):
            l.append(bstring[i:i+20])
            i += 20
        return l


class Torrent(object):
    """
    The Torrent object stores all information about an active torrent.
    It is initiallised with the dictionary values taken from the
    .torrent file
    """
    def __init__(self, tdict, info_string, directory, custom_name=None):
        self._info_string = info_string
        #print(info_string)
        self._info_hash = hashlib.sha1(info_string).digest()
        self._peer_id = _generate_peer_id()
        self._uploaded = 0
        self._downloaded = 0
        self._tracker_url = tdict[b'announce']
        self._piece_length = tdict[b'info'][b'piece length']
        if b'files' in tdict[b'info']: # multi-file case
            raise Exception("multi-file torrents not yet supported")
        else: # single file case
            # store hash and a bolean to mark if we have the piece or not
            self._torrent_name = bytes.decode(tdict[b'info'][b'name'])
            if custom_name:
                self._filename = os.path.join(directory, custom_name)
            else:
                self._filename = os.path.join(directory, self._torrent_name)
            self._pieces = [ (self._filename, i, sha1, False) 
                             for i, sha1 in enumerate(_parse_pieces(tdict[b'info'][b'pieces']))]
            self._file_length = int(tdict[b'info'][b'length'])
            self._left = self._file_length

        # info not from .torrent file
        self.peer_list = []
        self.interval = 100
        self.complete = 0
        self.incomplete = 0

    @property
    def info_hash(self):
        return self._info_hash

    @property
    def peer_id(self):
        return self._peer_id

    @property
    def tracker_url(self):
        # TODO - this is very lazy!
        return self._tracker_url.rsplit(b'/', 1)[0]

    @property
    def tracker_path(self):
        # TODO - this is very lazy!
        return self._tracker_url.rsplit(b'/', 1)[1]

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

