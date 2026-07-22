"""Sliding-window public rate limiter coverage."""

from public_agent.limiter import SlidingWindowRateLimiter


def test_sliding_window_releases_only_expired_requests() -> None:
    now = [0.0]
    limiter = SlidingWindowRateLimiter(
        requests=2,
        window_seconds=10,
        clock=lambda: now[0],
    )

    assert limiter.check("client").allowed is True
    now[0] = 5.0
    assert limiter.check("client").allowed is True
    now[0] = 9.0
    blocked = limiter.check("client")
    assert blocked.allowed is False
    assert blocked.retry_after_seconds == 1

    now[0] = 10.0
    released = limiter.check("client")
    assert released.allowed is True
    assert released.remaining == 0
