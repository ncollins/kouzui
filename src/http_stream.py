import logging
from typing import Type

import trio
import h11

from config import STREAM_CHUNK_SIZE

logger = logging.getLogger("httpstream")


class HttpStream:
    def __init__(self, stream: trio.abc.Stream, role: Type[h11.CLIENT | h11.SERVER]) -> None:
        self.stream: trio.abc.Stream = stream
        self.conn: h11.Connection = h11.Connection(our_role=role)
        logger.debug("h11 connection {}".format(self.conn))
        logger.debug("our role = {}".format(self.conn.our_role))
        logger.debug("their role = {}".format(self.conn.their_role))

    async def receive_event(self) -> h11.Event | type[h11.NEED_DATA] | type[h11.PAUSED]:
        while True:
            logger.debug("about to get h11 event...")
            e = self.conn.next_event()
            logger.debug('h11 event = "{}"'.format(str(e)))
            if e == h11.NEED_DATA:
                raw_bytes = await self.stream.receive_some(STREAM_CHUNK_SIZE)
                logger.debug('raw bytes = "{}"'.format(str(raw_bytes)))
                # if raw_bytes != b'':
                self.conn.receive_data(raw_bytes)
                logger.debug("sent data to h11 connection")
            else:
                return e

    async def receive_with_data(
        self,
    ) -> tuple[
        h11.Event | type[h11.NEED_DATA] | type[h11.PAUSED],
        list[h11.Event | type[h11.NEED_DATA] | type[h11.PAUSED]],
    ]:
        first_event = await self.receive_event()
        data = []
        next_event = await self.receive_event()
        while not isinstance(next_event, h11.EndOfMessage):
            data.append(next_event)
            next_event = await self.receive_event()
        return first_event, data

    async def send_event(self, e: h11.Event) -> None:
        raw_bytes = self.conn.send(e)
        if isinstance(e, h11.ConnectionClosed):
            await self.close()
            logger.debug("handled {} by closing stream".format(str))
        else:
            assert raw_bytes is not None
            await self.stream.send_all(raw_bytes)
            logger.debug("handled {} by sending {!r}".format(str(e), raw_bytes))

    async def close(self) -> None:
        # TODO 2026-03-02: consider whether this should functionality should only
        # be triggered by receiving h11.ConnectionClosed in `send_event`.
        await self.stream.aclose()
