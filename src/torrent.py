import os
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
def parse_pieces(bstring):
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
    def __init__(self, tdict, directory, custom_name=None):
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
                             for i, sha1 in enumerate(parse_pieces(tdict[b'info'][b'pieces']))]

    def piece_info(self, n):
        return self._pieces[n]
