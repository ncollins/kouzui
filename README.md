# Kouzui

A Bittorrent client written in Python to learn more about writing concurrent networking code.
I worked on this while attending [Recurse Center](https://www.recurse.com/) during the summer of 2018.
It uses the [Trio](https://trio.readthedocs.io) library for concurrent networking.

The original code, warts and all, is in the branch `v1-recurse-center-2018`.

In February 2026 I began updating the project as an experiment in using AI agents for maintenance 
work: updating dependencies, adding tests, and improving type checking. The branch `v1.5-tidy-up-with-ai-agent-help-2026` maintains the fundamental structure of 
`v1-recurse-center-2018`, but:
- introduces `uv` to manage the project and environment
- updates the code to work with Python >= 3.11 and the latest versions of `trio`, `h11` and `bitarray`.
- adds an integration test that uses Podman to run [the bittorrent-tracker npm package](https://www.npmjs.com/package/bittorrent-tracker)
  and multiple Kouzui clients (previously I had run these manually) 
- contains many small QoL improvements to the codebase:
  - adds type annotations for all functions and methods
  - adds dataclasses to replace unstructure data being passed around in tuples
  - removes all unused imports and most of the commented out code 

## Installation and running

`kouzui` can easily be installed if you have `uv` on your system:

```sh
uv tool install .
```
and then run with:
```sh
kouzui run path/to/torrent_file.torrent --download-dir path/to/downloads
```

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
