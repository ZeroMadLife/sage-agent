"""Small in-process sliding-window limiter for the single-process public Agent."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class SlidingWindowRateLimiter:
    def __init__(
        self,
        *,
        requests: int = 12,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests < 1 or window_seconds < 1:
            raise ValueError("rate limit values must be positive")
        self.requests = requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._entries: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, client_key: str) -> RateLimitDecision:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            entries = self._entries[client_key]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= self.requests:
                retry_after = max(1, int(self.window_seconds - (now - entries[0]) + 0.999))
                return RateLimitDecision(False, 0, retry_after)
            entries.append(now)
            return RateLimitDecision(True, self.requests - len(entries), 0)
