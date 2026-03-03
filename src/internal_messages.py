from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True, slots=True)
class CompletePieceToWrite:
    index: int
    data: bytes


@dataclass(frozen=True, kw_only=True, slots=True)
class AllPiecesWritten:
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class WriteConfirmation:
    index: int


@dataclass(frozen=True, kw_only=True, slots=True)
class BlockToRead:
    peer_id: bytes
    index: int
    begin: int
    length: int


@dataclass(frozen=True, kw_only=True, slots=True)
class BlockForPeer:
    peer_id: bytes
    index: int
    begin: int
    length: int
    data: bytes
