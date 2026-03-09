import logging

import trio

logger = logging.getLogger("token_bucket")


# class NullBucket(object):
#    def __init__(self):
#        pass
#
#    def check_and_decrement(self, _packet_size):
#        return True
#
#    async def loop(self):
#        pass
#


class TokenBucket(object):
    def __init__(
        self,
        bytes_per_second: int,
        max_size_in_bytes: int | None = None,
        updates_per_second: int = 10,
    ) -> None:
        self.bucket: float = 0
        self.max_size_in_bytes = max_size_in_bytes if max_size_in_bytes else 2 * bytes_per_second
        self.bytes_per_second = bytes_per_second
        self.updates_per_second = updates_per_second

    @property
    def _update_period(self) -> float:
        return 1.0 / self.updates_per_second

    def _check_and_decrement(self, packet_size: int) -> bool:
        if self.bucket >= packet_size:
            self.bucket -= packet_size
            return True
        else:
            return False

    async def wait_for_approval(self, packet_size: int) -> None:
        while not self._check_and_decrement(packet_size):
            logger.debug("Token bucket is empty waiting, waiting for {self.update_period}s")
            await trio.sleep(self._update_period)

    async def loop(self) -> None:
        while True:
            await trio.sleep(self._update_period)
            increment = self.bytes_per_second / self.updates_per_second
            self.bucket = min(self.bucket + increment, self.max_size_in_bytes)
