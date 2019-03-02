# Kouzui

A Bittorrent client written in Python to learn more about writing concurrent networking code.
I worked on this while attending [Recurse Center](https://www.recurse.com/) during the summer of 2018.
It uses the [Trio](https://trio.readthedocs.io) library for concurrent networking.

## Installation and running

Kouzui uses the async/await syntax added in Python 3.5, so it will not run on earlier versions of Python.
It has only been tested with Python 3.6 and Python 3.7, on GNU/Linux (Ubuntu) and Mac OS.

The requirements are specified in `requirements.txt` and can be installed with `pip install -r requirements.txt`.
The `bitarray` module is a C-extension so it may need a C compiler and Python developer tools to be installed
(e.g. Ubuntu will need `python3-dev` installed in addition to `python3`).

It can then be run from the project directory with:

`python src/main.py run path/to/torrent_file.torrent --download-dir path/to/downloads`

## What it can do

Client features:
- Load Torrent information from a .torrent file
- Get peer information from a traker over the HTTP protocol
- Connect to multiple peers and concurrently download/upload
- Check the hashes of received pieces to make sure they are valid
- Send and receive "HAVE" messages (used to update knowledge of which peers have which pieces)
- Choke uploads to peers giving poor download rates
- Resume incomplete downloads
- Rate limiting (using a basic [token bucket](https://en.wikipedia.org/wiki/Token_bucket) implementation)

Testing features:
- Split a file into multiple incomplete files
- Run multiple versions of the client simultaneously using the multiprocessing module.

## TODO list

See the [issues page](https://github.com/ncollins/kouzui/issues).

## Features I don't intend to add

There are a number of features that would be required in a "complete" Bittorrent client,
but I consider orthogonal to my learning goals for this project, including:

- Support for torrents that contain multiple files
- Support for magnet links
- Connecting to the tracker over protocols other than HTTP
- Downloading rarest pieces first
- An explicit "endgame" strategy - some Bittorrent clients have explict logic for requesting
the final piece from multiple peers, the current randomized requests strategry does this automatically
(at the cost of occasionally sending unnecessary duplicate requests earlier in the download).
