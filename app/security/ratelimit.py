"""P7.3: rate limiting port and a bounded in-memory token-bucket adapter.

``check(key)`` consumes one token and returns ``None`` if allowed, or the number
of seconds after which a retry can succeed. Memory is bounded: at most
``max_keys`` buckets are kept (least recently used evicted; an evicted key just
starts again with a full bucket, which never penalises anyone).

The in-memory adapter limits ONE process. Several replicas need a shared
limiter (e.g. at the reverse proxy/API gateway, or a Redis/PostgreSQL adapter):
pending, documented in docs/SECURITY.md.
"""
import math
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock


class RateLimiter(ABC):
    @abstractmethod
    def check(self, key: str) -> float | None:
        """Consume one unit for ``key``; ``None`` if allowed, else seconds to wait."""


class TokenBucketLimiter(RateLimiter):
    def __init__(self, per_minute: int, burst: int | None = None, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic):
        if type(per_minute) is not int or per_minute < 1:
            raise ValueError("per_minute must be a positive integer")
        self.capacity = float(burst if burst is not None else max(1, per_minute // 4))
        if self.capacity < 1 or max_keys < 1:
            raise ValueError("burst and max_keys must be positive")
        self.rate = per_minute / 60.0  # tokens per second
        self.max_keys = max_keys
        self._clock = clock
        self._lock = Lock()
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def check(self, key: str) -> float | None:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.pop(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                retry = None
            else:
                self._buckets[key] = (tokens, now)
                retry = max(1.0, math.ceil((1.0 - tokens) / self.rate))
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return retry

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)
