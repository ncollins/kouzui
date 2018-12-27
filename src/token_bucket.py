import trio

class TokenBucket(object):
    def __init__(self, bytes_per_second, max_size_in_bytes=None, updates_per_second=10):
        self.bucket = 0
        self.max_size_in_bytes = max_size_in_bytes if max_size_in_bytes else 2 * bytes_per_second
        self.bytes_per_second = bytes_per_second
        self.updates_per_second = updates_per_second

    def check_and_decrement(self, packet_size):
        if self.bucket >= packet_size:
            self.bucket -= packet_size
            return True
        else:
            return False

    async def loop(self):
        while True:
            await trio.sleep(1.0 / self.updates_per_second)
            increment = self.bytes_per_second / self.updates_per_second
            self.bucket = min(self.bucket + increment, self.max_size_in_bytes)
