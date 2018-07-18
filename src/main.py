
import argparse
import bencode



def main():
    argparser = argparse.ArgumentParser()
    argparser.add_argument('torrent_path', help='path to the .torrent file')
    args = argparser.parse_args()
    with open(args.torrent_path, 'rb') as f:
        torrent_data = bencode.parse_value(f)
        print(torrent_data.keys())
        print(torrent_data[b'info'].keys())



if __name__ == '__main__':
    main()
