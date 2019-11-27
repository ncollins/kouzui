import argparse
import hashlib
import logging
import multiprocessing as mp
import pathlib
import random
import shutil
import os
import time

import trio

import bencode
import engine
import file_manager
from torrent import Torrent

logger = logging.getLogger("main")


def read_torrent_file(torrent_path):
    with open(torrent_path, "rb") as f:
        torrent_data: dict = bencode.parse_value(f)
        logger.debug("torrent_data = {}".format(torrent_data))
    torrent_info = bencode.encode_value(torrent_data[b"info"])
    logger.debug("torrent info = {}".format(torrent_info))
    # if True:
    #    with open(args.torrent_path, 'rb') as f:
    #        raw = f.read()
    #        logger.debug("info_string matches {}".format(torrent_info in raw))
    return (torrent_data, torrent_info)


def run(log_level, torrent_path, listening_port, download_dir):
    if log_level:
        log_level = getattr(logging, log_level.upper())
    else:
        log_level = getattr(logging, "WARNING")
    logfile = "tmp/{}.log".format(listening_port)  # TODO - directory shouldn't be hardcoded
    logging.basicConfig(
        filename=logfile,
        level=log_level,
        format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d `%(funcName)s` -- %(message)s",
    )
    torrent_data, torrent_info = read_torrent_file(torrent_path)
    download_dir = download_dir if download_dir else os.path.dirname(os.path.abspath(__file__))
    port = int(listening_port) if listening_port else None
    t = Torrent(torrent_data, torrent_info, download_dir, port)
    engine.run(t)


def run_command(args):
    run(args.log_level, args.torrent_path, args.listening_port, args.download_dir)


def make_test_files(torrent_data, torrent_info, download_dir, number_of_files):
    t = Torrent(torrent_data, torrent_info, download_dir, None)
    files = []
    main_file_wrapper = file_manager.FileWrapper(torrent=t, file_suffix="")
    main_file_wrapper.create_file_or_return_hashes()
    for i in range(int(number_of_files)):
        fw = file_manager.FileWrapper(torrent=t, file_suffix=".{}".format(i))
        fw.create_file_or_return_hashes()
        files.append(fw)
    for p in t._pieces:
        data = main_file_wrapper.read_block(p.index, 0, t.piece_length(p.index))
        if p.sha1hash == hashlib.sha1(data).digest():
            random.choice(files).write_piece(p.index, data)


def make_test_files_command(args):
    torrent_data, torrent_info = read_torrent_file(args.torrent_path)
    download_dir = (
        args.download_dir if args.download_dir else os.path.dirname(os.path.abspath(__file__))
    )
    number_of_files = args.number_of_files
    make_test_files(torrent_data, torrent_info, download_dir, number_of_files)


def test(test_dir, torrent_path, number_of_clients):
    test_dir = pathlib.Path(test_dir)
    # TODO separate timing of file copy and torrenting
    start_time = time.perf_counter()
    torrent_data, torrent_info = read_torrent_file(torrent_path)
    # ----- RUN CLIENTS ----------------
    client_processes = []
    for i in range(number_of_clients):
        client_dir = test_dir / "clients" / ("client-{}".format(i))
        client_dir.mkdir(exist_ok=True, parents=True)

        # TODO tidy up potential torrent_name, custom_name issues
        torrent_name = bytes.decode(torrent_data[b"info"][b"name"])
        test_file = test_dir / "{}.{}.part".format(torrent_name, i)
        tmp_file = client_dir / "{}.part".format(torrent_name)
        final_file = client_dir / torrent_name

        try:
            os.remove(final_file)
        except FileNotFoundError:
            pass

        shutil.copy(test_file, tmp_file)

        p = mp.Process(target=run, args=("WARNING", torrent_path, 50000 + i, client_dir))
        client_processes.append((p, final_file, tmp_file))

    torrent_start_time = time.perf_counter()
    for p, _, _ in client_processes:
        p.start()
    # Wait for clients to complete and shutdown
    while not all(os.path.exists(final_location) for _, final_location, _ in client_processes):
        time.sleep(1)
    end_time = time.perf_counter()
    for p, _, _ in client_processes:
        p.terminate()
    print(
        "{} clients finished, {} seconds (setup file copy took {} second)".format(
            number_of_clients, end_time - torrent_start_time, torrent_start_time - start_time
        )
    )


def test_command(args):
    test(args.test_dir, args.torrent_path, int(args.number_of_clients))


def main():
    argparser = argparse.ArgumentParser()
    # run sub-command ----------------------
    sub_commands = argparser.add_subparsers(help="sub-commands help")
    run = sub_commands.add_parser("run", help="Run Bittorrent client")
    run.add_argument("torrent_path", help="path to the .torrent file")
    run.add_argument("--listening-port", help="listening port for incoming peer connections")
    run.add_argument("--log-level", help="DEBUG/INFO/WARNING")
    run.add_argument("--download-dir", help="directory to save the file in")
    run.set_defaults(func=run_command)
    # make-test-files sub-command ----------
    make_test_files = sub_commands.add_parser(
        "make-test-files", help="Split a complete file into incomplete files for testing"
    )
    make_test_files.add_argument("torrent_path", help="path to the .torrent file")
    make_test_files.add_argument("--number-of-files", help="number of files to create")
    make_test_files.add_argument(
        "--download-dir", help="directory to find the complete file and save the incomplete files"
    )
    make_test_files.set_defaults(func=make_test_files_command)
    # test-clients sub-command
    test = sub_commands.add_parser(
        "test-run", help="Run multiple clients in separate processes for testing"
    )
    test.add_argument("torrent_path", help="path to the .torrent file")
    test.add_argument("--test-dir", help="number of clients")
    test.add_argument("--number-of-clients", help="number of clients")
    test.set_defaults(func=test_command)
    # --------------------------------------
    args = argparser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
