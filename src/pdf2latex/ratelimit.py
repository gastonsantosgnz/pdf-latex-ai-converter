"""Thread-safe token-bucket rate limiting for the conversion pool.

Replaces the old fixed ``time.sleep(1.0)`` between requests. Two optional limits
can be enforced together: requests per minute (RPM) and tokens per minute (TPM).
Both are off by default, in which case :meth:`RateLimiter.acquire` is a no-op and
the worker count is the only throttle; the provider's own 429s are still handled
by the exponential backoff in :func:`pdf2latex.worker._create_with_backoff`.

The clock and sleep functions are injectable so the limiter can be tested
deterministically without real time passing.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class _Bucket:
    """A single token bucket: ``capacity`` tokens, refilled at ``rate``/second."""

    def __init__(self, rate_per_sec: float, capacity: float, time_fn: Callable[[], float]) -> None:
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = capacity
        self.time_fn = time_fn
        self.stamp = time_fn()

    def _refill(self) -> None:
        now = self.time_fn()
        if now > self.stamp:
            self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.rate)
            self.stamp = now

    def wait_for(self, need: float) -> float:
        """Seconds until ``need`` tokens are available (0.0 if already)."""
        self._refill()
        if self.tokens >= need:
            return 0.0
        return (need - self.tokens) / self.rate


class RateLimiter:
    """Enforce optional RPM and/or TPM limits across all worker threads."""

    def __init__(
        self,
        *,
        rpm: float | None = None,
        tpm: float | None = None,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._lock = threading.Lock()
        self._sleep = sleep_fn
        self._req = _Bucket(rpm / 60.0, max(1.0, rpm / 60.0), time_fn) if rpm and rpm > 0 else None
        self._tok = _Bucket(tpm / 60.0, float(tpm), time_fn) if tpm and tpm > 0 else None

    @property
    def enabled(self) -> bool:
        return self._req is not None or self._tok is not None

    def acquire(self, tokens: int = 0) -> None:
        """Block until one request (and ``tokens`` tokens) may proceed."""
        if not self.enabled:
            return
        with self._lock:
            while True:
                waits: list[float] = []
                if self._req is not None:
                    waits.append(self._req.wait_for(1))
                if self._tok is not None and tokens > 0:
                    waits.append(self._tok.wait_for(tokens))
                wait = max(waits) if waits else 0.0
                if wait <= 0:
                    if self._req is not None:
                        self._req.tokens -= 1
                    if self._tok is not None and tokens > 0:
                        self._tok.tokens -= tokens
                    return
                self._sleep(wait)
