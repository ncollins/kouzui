from collections import OrderedDict
import hashlib
import logging
import multiprocessing as mp
from pathlib import Path
import random
import shutil
import time
from typing import Any, Optional, cast

import typer

import bencode
import engine
import file_manager
from torrent import Torrent

logger = logging.getLogger("main")

app = typer.Typer()


def read_torrent_file(torrent_file: Path) -> tuple[OrderedDict[bytes, Any], bytes]:
    with torrent_file.open("rb") as f:
        torrent_data: OrderedDict[bytes, Any] = cast(
            OrderedDict[bytes, Any], bencode.parse_value(f)
        )
        logger.debug(f"torrent_data = {torrent_data}")
    torrent_info = bencode.encode_value(torrent_data[b"info"])
    logger.debug(f"torrent info = {torrent_info!r}")
    # if True:
    #    with open(args.torrent_path, 'rb') as f:
    #        raw = f.read()
    #        logger.debug("info_string matches {}".format(torrent_info in raw))
    return (torrent_data, torrent_info)


def run(
    log_level: str | None,
    torrent_file: Path,
    listening_port: Optional[int],
    download_dir: Optional[Path],
    auto_shutdown: bool,
):
    if log_level is not None:
        log_level = getattr(logging, log_level.upper())
    else:
        log_level = getattr(logging, "WARNING")
    # TODO 2026-03-01: ideally the log location isn't tied to the download location
    # but this is better for testing than writing to /tmp in a container
    logfile = f"{download_dir}/{listening_port}.log"
    logging.basicConfig(
        filename=logfile,
        level=log_level,
        format="%(asctime)s %(levelname)s %(filename)s:%(lineno)d `%(funcName)s` -- %(message)s",
    )
    torrent_data, torrent_info = read_torrent_file(torrent_file)
    download_dir = download_dir if download_dir else Path.cwd()
    port = int(listening_port) if listening_port else None
    t = Torrent(torrent_data, torrent_info, download_dir, port)
    engine.run(t, auto_shutdown=auto_shutdown)


def make_test_files(
    torrent_data: OrderedDict[bytes, Any],
    torrent_info: bytes,
    download_dir: Path,
    number_of_files: int,
):
    t = Torrent(torrent_data, torrent_info, download_dir, None)
    files = []
    main_file_wrapper = file_manager.FileWrapper(torrent=t, file_suffix="")
    main_file_wrapper.create_file_or_return_hashes()
    for i in range(number_of_files):
        fw = file_manager.FileWrapper(torrent=t, file_suffix=f".{i}")
        fw.create_file_or_return_hashes()
        files.append(fw)
    for p in t._pieces:
        data = main_file_wrapper.read_block(p.index, 0, t.piece_length(p.index))
        if p.sha1hash == hashlib.sha1(data).digest():
            random.choice(files).write_piece(p.index, data)


def test(test_dir: Path, torrent_file: Path, number_of_clients: int):
    # TODO separate timing of file copy and torrenting
    start_time = time.perf_counter()
    torrent_data, _torrent_info = read_torrent_file(torrent_file)
    # ----- RUN CLIENTS ----------------
    client_processes: list[tuple[mp.Process, Path, Path]] = []
    for i in range(number_of_clients):
        client_dir = test_dir / "clients" / f"client-{i}"
        client_dir.mkdir(exist_ok=True, parents=True)

        # TODO tidy up potential torrent_name, custom_name issues
        torrent_name = bytes.decode(torrent_data[b"info"][b"name"])
        test_file = test_dir / f"{torrent_name}.{i}.part"
        tmp_file = client_dir / f"{torrent_name}.part"
        final_file = client_dir / torrent_name

        try:
            final_file.unlink()
        except FileNotFoundError:
            pass

        shutil.copy(test_file, tmp_file)

        p = mp.Process(target=run, args=("INFO", torrent_file, 50000 + i, client_dir, False))
        client_processes.append((p, final_file, tmp_file))

    torrent_start_time = time.perf_counter()
    for p, _, _ in client_processes:
        p.start()
    # Wait for clients to complete and shutdown
    # TODO 2026-03-03: this code checking that all the clients have completed could be
    # replaced by the auto_shutdown argument passed to engine.run
    while not all(final_location.exists() for _, final_location, _ in client_processes):
        time.sleep(1)
    end_time = time.perf_counter()
    for p, _, _ in client_processes:
        p.terminate()
    print(
        f"{number_of_clients} clients finished, {end_time - torrent_start_time} seconds (setup file copy took {torrent_start_time - start_time} second)"
    )


@app.command("run")
def run_command(
    torrent_file: Path = typer.Argument(help="path to the .torrent file"),
    listening_port: Optional[int] = typer.Option(
        None, "--listening-port", help="listening port for incoming peer connections"
    ),
    log_level: Optional[str] = typer.Option(None, "--log-level", help="DEBUG/INFO/WARNING"),
    download_dir: Optional[Path] = typer.Option(
        None, "--download-dir", help="directory to save the file in"
    ),
    auto_shutdown: bool = typer.Option(
        False, "--auto-shutdown", help="automatically shutdown if there are no peers downloading"
    ),
):
    """Run Bittorrent client"""
    run(log_level, torrent_file, listening_port, download_dir, auto_shutdown)


@app.command("make-test-files")
def make_test_files_command(
    torrent_file: Path = typer.Argument(help="path to the .torrent file"),
    number_of_files: int = typer.Option(
        None, "--number-of-files", help="number of files to create"
    ),
    download_dir: Optional[Path] = typer.Option(
        None,
        "--download-dir",
        help="directory to find the complete file and save the incomplete files",
    ),
):
    """Split a complete file into incomplete files for testing"""
    torrent_data, torrent_info = read_torrent_file(torrent_file)
    dl_dir = (
        download_dir if download_dir else Path.cwd()
    )  # os.path.dirname(os.path.abspath(__file__))
    make_test_files(torrent_data, torrent_info, dl_dir, number_of_files)


@app.command("test-run")
def test_run_command(
    torrent_path: Path = typer.Argument(help="path to the .torrent file"),
    test_dir: Path = typer.Option(None, "--test-dir", help="test directory"),
    number_of_clients: int = typer.Option(..., "--number-of-clients", help="number of clients"),
):
    """Run multiple clients in separate processes for testing"""
    test(test_dir, torrent_path, number_of_clients)


def main():
    app()


if __name__ == "__main__":
    main()
