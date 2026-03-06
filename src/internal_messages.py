from dataclasses import dataclass
from typing import TypeAlias

import trio

from peer_messages import PeerMessage
from shared_types import Block, PeerId


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
    peer_id: PeerId
    block: Block


@dataclass(frozen=True, kw_only=True, slots=True)
class HandshakeComplete:
    peer_id: PeerId
    send_channel: trio.MemorySendChannel[PeerMessage]


@dataclass(frozen=True, kw_only=True, slots=True)
class PeerConnectionClosed:
    peer_id: PeerId


@dataclass(frozen=True, kw_only=True, slots=True)
class PeerMsg:
    peer_id: PeerId
    msg: PeerMessage


EngineMessage: TypeAlias = HandshakeComplete | PeerConnectionClosed | PeerMsg
