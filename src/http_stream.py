import logging

import h11

from config import STREAM_CHUNK_SIZE

logger = logging.getLogger('httpstream')

class Http_stream(object):
    def __init__(self, stream, role):
        self.stream = stream
        self.conn = h11.Connection(our_role=role)
        logger.debug('h11 connection {}'.format(self.conn))
        logger.debug('our role = {}'.format(self.conn.our_role))
        logger.debug('their role = {}'.format(self.conn.their_role))

    async def receive_event(self):
        while True:
            logger.debug('about to get h11 event...')
            e = self.conn.next_event()
            logger.debug('h11 event = "{}"'.format(str(e)))
            if e == h11.NEED_DATA:
                raw_bytes = await self.stream.receive_some(STREAM_CHUNK_SIZE)
                logger.debug('raw bytes = "{}"'.format(str(raw_bytes)))
                #if raw_bytes != b'':
                self.conn.receive_data(raw_bytes)
                logger.debug('sent data to h11 connection')
            else:
                return e

    async def receive_with_data(self):
        first_event = await self.receive_event()
        data = []
        next_event = await self.receive_event()
        while not isinstance(next_event, h11.EndOfMessage):
            data.append(next_event)
            next_event = await self.receive_event()
        return first_event, data

    async def send_event(self, e):
        raw_bytes = self.conn.send(e)
        await self.stream.send_all(raw_bytes)
        logger.debug("sent {} as {}".format(str(e), str(raw_bytes)))

    async def close(self):
        await self.stream.aclose()
