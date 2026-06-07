"""Tests for the token-bucket rate limiter (deterministic, fake clock)."""

from __future__ import annotations

import pytest

from pdf2latex.ratelimit import RateLimiter


class FakeClock:
    """A controllable monotonic clock; ``sleep`` simply advances time."""

    def __init__(self) -> None:
        self.t = 0.0

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _limiter(clock: FakeClock, **kwargs) -> RateLimiter:
    return RateLimiter(time_fn=clock.time, sleep_fn=clock.sleep, **kwargs)


def test_disabled_limiter_is_a_noop() -> None:
    clk = FakeClock()
    rl = _limiter(clk)
    assert rl.enabled is False
    rl.acquire(99_999)
    assert clk.t == 0.0  # never slept


def test_rpm_spaces_requests() -> None:
    clk = FakeClock()
    rl = _limiter(clk, rpm=60)  # 1 request/second, capacity 1
    rl.acquire()
    assert clk.t == 0.0  # first request is immediate
    rl.acquire()
    assert clk.t == pytest.approx(1.0)  # second waits ~1s
    rl.acquire()
    assert clk.t == pytest.approx(2.0)


def test_tpm_limits_token_throughput() -> None:
    clk = FakeClock()
    rl = _limiter(clk, tpm=600)  # 10 tokens/second, capacity 600
    rl.acquire(600)  # drains the bucket
    assert clk.t == 0.0
    rl.acquire(100)  # must wait 100 / 10 = 10s for a refill
    assert clk.t == pytest.approx(10.0)


def test_rpm_allows_burst_up_to_capacity() -> None:
    clk = FakeClock()
    rl = _limiter(clk, rpm=120)  # 2/sec, capacity max(1, 2) = 2
    rl.acquire()
    rl.acquire()
    assert clk.t == 0.0  # two-request burst is free
    rl.acquire()
    assert clk.t == pytest.approx(0.5)  # third waits for one refill at 2/sec
