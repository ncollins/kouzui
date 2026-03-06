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
    def update_period(self) -> float:
        return 1.0 / self.updates_per_second

    def check_and_decrement(self, packet_size: int) -> bool:
        if self.bucket >= packet_size:
            self.bucket -= packet_size
            return True
        else:
            return False

    async def loop(self) -> None:
        while True:
            await trio.sleep(self.update_period)
            increment = self.bytes_per_second / self.updates_per_second
            self.bucket = min(self.bucket + increment, self.max_size_in_bytes)
