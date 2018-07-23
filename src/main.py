
import argparse
import os

import bencode
import networking
from torrent import Torrent

def main():
    current_directory = os.path.dirname(os.path.abspath(__file__))
    argparser = argparse.ArgumentParser()
    argparser.add_argument('torrent_path', help='path to the .torrent file')
    args = argparser.parse_args()
    with open(args.torrent_path, 'rb') as f:
        torrent_data = bencode.parse_value(f)
    torrent_info = bencode.encode_value(torrent_data[b'info'])
    if True:
        with open(args.torrent_path, 'rb') as f:
            raw = f.read()
            print("info_string matches {}".format(torrent_info in raw))

    
    #print(torrent_data.keys())
    #print(torrent_data[b'info'].keys())
    #print(torrent_data[b'info'][b'name'])
    t = Torrent(torrent_data, torrent_info, current_directory)
    #print(t.piece_info(1))
    #print(t.tracker_url)
    networking.run(t)


if __name__ == '__main__':
    main()
