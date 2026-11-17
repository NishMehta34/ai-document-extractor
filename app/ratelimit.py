"""In-memory rate limiter: at most N requests per client per hour.

Enough for a single-server public demo, to keep model costs under control.
(A multi-server deployment would need a shared store such as Redis.)
"""

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int = 3600, clock=time.monotonic):
        self.max_requests = max_requests
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, client: str) -> tuple[bool, int]:
        """Return (allowed, seconds_until_next_slot)."""
        if self.max_requests <= 0:
            return True, 0
        now = self.clock()
        hits = self._hits[client]
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return False, int(self.window - (now - hits[0])) + 1
        hits.append(now)
        return True, 0
