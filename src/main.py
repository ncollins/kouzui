import argparse
import hashlib
import logging
import random
import os

import trio

import bencode
import engine
import file_manager
from torrent import Torrent

logger = logging.getLogger('main')

def read_torrent_file(torrent_path):
    with open(torrent_path, 'rb') as f:
        torrent_data : dict = bencode.parse_value(f)
        logger.debug('torrent_data = {}'.format(torrent_data))
    torrent_info = bencode.encode_value(torrent_data[b'info'])
    logger.debug('torrent info = {}'.format(torrent_info))
    #if True:
    #    with open(args.torrent_path, 'rb') as f:
    #        raw = f.read()
    #        logger.debug("info_string matches {}".format(torrent_info in raw))
    return (torrent_data, torrent_info)

def run_command(args):
    if args.log_level:
        log_level = getattr(logging, args.log_level.upper())
    else:
        log_level = getattr(logging, 'WARNING')
    logfile = 'tmp/{}.log'.format(args.listening_port) # TODO - directory shouldn't be hardcoded
    logging.basicConfig(filename=logfile, level=log_level, format='%(asctime)s %(levelname)s %(filename)s:%(lineno)d `%(funcName)s` -- %(message)s')
    torrent_data, torrent_info = read_torrent_file(args.torrent_path)
    download_dir = args.download_dir if args.download_dir else os.path.dirname(os.path.abspath(__file__))
    port = int(args.listening_port) if args.listening_port else None
    t = Torrent(torrent_data, torrent_info, download_dir, port)
    engine.run(t)

def make_test_files_command(args):
    print(args)
    torrent_data, torrent_info = read_torrent_file(args.torrent_path)
    download_dir = args.download_dir if args.download_dir else os.path.dirname(os.path.abspath(__file__))
    t = Torrent(torrent_data, torrent_info, download_dir, None)
    files = []
    dummy_queue = trio.Queue(1)
    main_fm = file_manager.FileManager(t, dummy_queue, dummy_queue, dummy_queue, dummy_queue)
    main_fm.create_file_or_return_hashes()
    for i in range(int(args.number_of_files)):
        fm = file_manager.FileManager(t, dummy_queue, dummy_queue, dummy_queue, dummy_queue, file_suffix='.{}'.format(i))
        fm.create_file_or_return_hashes()
        files.append(fm)
    for p in t._pieces:
        data = main_fm.read_block(p.index, 0, t.piece_length(p.index))
        if p.sha1hash == hashlib.sha1(data).digest():
            random.choice(files).write_piece(p.index, data)

def main():
    argparser = argparse.ArgumentParser()
    # run sub-command ----------------------
    sub_commands = argparser.add_subparsers(help="sub-commands help")
    run = sub_commands.add_parser("run", help="Run Bittorrent client")
    run.add_argument('torrent_path', help='path to the .torrent file')
    run.add_argument('--listening-port', help='listening port for incoming peer connections')
    run.add_argument('--log-level', help='DEBUG/INFO/WARNING')
    run.add_argument('--download-dir', help='directory to save the file in')
    run.set_defaults(func=run_command)
    # make-test-files sub-command ----------
    make_test_files = sub_commands.add_parser("make-test-files", help="Split a complete file into incomplete files for testing")
    make_test_files.add_argument('torrent_path', help='path to the .torrent file')
    make_test_files.add_argument('--number-of-files', help='number of files to create')
    make_test_files.add_argument('--download-dir', help='directory to find the complete file and save the incomplete files')
    make_test_files.set_defaults(func=make_test_files_command)
    # --------------------------------------
    args = argparser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
