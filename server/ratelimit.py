"""Token-bucket rate limiting, keyed per user.

Used to throttle each logged-in user's outbound calls to Google (sending chat),
both to protect the user's account from spam flags and to stop a runaway client
from hammering the API. A token bucket allows a small human burst while capping
the sustained rate.

The clock is injectable so behaviour is deterministic in tests. All operations
are synchronous and non-awaiting, so they're atomic under our single-threaded
asyncio event loop (we run one worker).
"""

import time


class TokenBucket:
    def __init__(self, capacity: float, refill_per_sec: float, clock=time.monotonic):
        self.capacity = capacity
        self.refill = refill_per_sec
        self._clock = clock
        self.tokens = capacity
        self.updated = clock()

    def _refilled(self, now: float) -> float:
        return min(self.capacity, self.tokens + (now - self.updated) * self.refill)

    def allow(self, cost: float = 1.0) -> bool:
        now = self._clock()
        self.tokens = self._refilled(now)
        self.updated = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def retry_after(self, cost: float = 1.0) -> float:
        """Seconds until `cost` tokens are available (0 if available now)."""
        tokens = self._refilled(self._clock())
        if tokens >= cost:
            return 0.0
        return (cost - tokens) / self.refill


class RateLimiter:
    """A token bucket per key (e.g. per auth-token / user)."""

    def __init__(self, capacity: float, refill_per_sec: float, clock=time.monotonic):
        self._capacity = capacity
        self._refill = refill_per_sec
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket(self, key: str) -> TokenBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(self._capacity, self._refill, self._clock)
            self._buckets[key] = bucket
        return bucket

    def allow(self, key: str, cost: float = 1.0) -> bool:
        return self._bucket(key).allow(cost)

    def retry_after(self, key: str, cost: float = 1.0) -> float:
        return self._bucket(key).retry_after(cost)
