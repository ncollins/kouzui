import argparse
import logging
import os

import bencode
import engine
from torrent import Torrent

logger = logging.getLogger('main')

def main():
    argparser = argparse.ArgumentParser()
    argparser.add_argument('torrent_path', help='path to the .torrent file')
    argparser.add_argument('--output_dir', help='directory to save the file(s) in')
    argparser.add_argument('--listening_port', help='path to the .torrent file')
    argparser.add_argument('--log_level', help='INFO/DEBUG/WARNING etc.')
    args = argparser.parse_args()

    if args.log_level:
        log_level = getattr(logging, args.log_level.upper())
    else:
        log_level = getattr(logging, 'WARNING')
    logfile = 'tmp/{}.log'.format(args.listening_port)
    logging.basicConfig(filename=logfile, level=log_level, format='%(asctime)s %(message)s')

    with open(args.torrent_path, 'rb') as f:
        torrent_data : dict = bencode.parse_value(f)
        logger.debug('torrent_data = {}'.format(torrent_data))
    torrent_info = bencode.encode_value(torrent_data[b'info'])
    logger.debug('torrent info = {}'.format(torrent_info))
    if True:
        with open(args.torrent_path, 'rb') as f:
            raw = f.read()
            logger.info("info_string matches {}".format(torrent_info in raw))

    output_dir = args.output_dir if args.output_dir else os.path.dirname(os.path.abspath(__file__))
    
    port = int(args.listening_port) if args.listening_port else None
    t = Torrent(torrent_data, torrent_info, output_dir, port)
    engine.run(t)

if __name__ == '__main__':
    main()
