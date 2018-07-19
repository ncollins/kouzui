
import argparse
import os

import bencode
from torrent import Torrent

def main():
    current_directory = os.path.dirname(os.path.abspath(__file__))
    argparser = argparse.ArgumentParser()
    argparser.add_argument('torrent_path', help='path to the .torrent file')
    args = argparser.parse_args()
    with open(args.torrent_path, 'rb') as f:
        torrent_data = bencode.parse_value(f)
        print(torrent_data.keys())
        print(torrent_data[b'info'].keys())
        print(torrent_data[b'info'][b'name'])
        t = Torrent(torrent_data, current_directory)
        print(t.piece_info(1))
        #print(torrent_data[b'info'])
        #pieces = parse_pieces(torrent_data[b'info'][b'pieces'])
        #print(len(pieces))
        #print(pieces[0:3])



if __name__ == '__main__':
    main()
