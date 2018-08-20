
import argparse
import os

import bencode
import engine
from torrent import Torrent

def main():
    current_directory = os.path.dirname(os.path.abspath(__file__))
    argparser = argparse.ArgumentParser()
    argparser.add_argument('torrent_path', help='path to the .torrent file')
    argparser.add_argument('--listening_port', help='path to the .torrent file')
    args = argparser.parse_args()
    with open(args.torrent_path, 'rb') as f:
        torrent_data : dict = bencode.parse_value(f)
        print('torrent_data = {}'.format(torrent_data))
    torrent_info = bencode.encode_value(torrent_data[b'info'])
    print('torrent info = {}'.format(torrent_info))
    if True:
        with open(args.torrent_path, 'rb') as f:
            raw = f.read()
            print("info_string matches {}".format(torrent_info in raw))

    
    #print(torrent_data.keys())
    #print(torrent_data[b'info'].keys())
    #print(torrent_data[b'info'][b'name'])
    port = int(args.listening_port) if args.listening_port else None
    t = Torrent(torrent_data, torrent_info, current_directory, port)
    #print(t.piece_info(1))
    #print(t.tracker_url)
    engine.run(t)

if __name__ == '__main__':
    main()
