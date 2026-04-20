"""Tests for replicate_mcp.ratelimit — token bucket rate limiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from replicate_mcp.ratelimit import RateLimiter, TokenBucket

# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucket:
    def test_initial_tokens_full(self) -> None:
        bucket = TokenBucket(rate=10.0, capacity=20.0)
        assert bucket.available_tokens == pytest.approx(20.0, abs=0.1)

    def test_try_acquire_success(self) -> None:
        bucket = TokenBucket(rate=10.0, capacity=20.0)
        assert bucket.try_acquire(5.0) is True
        assert bucket.available_tokens == pytest.approx(15.0, abs=0.1)

    def test_try_acquire_fails_when_empty(self) -> None:
        bucket = TokenBucket(rate=10.0, capacity=5.0)
        bucket.try_acquire(5.0)
        assert bucket.try_acquire(1.0) is False

    def test_try_acquire_with_refill(self) -> None:
        bucket = TokenBucket(rate=100.0, capacity=10.0)
        bucket.try_acquire(10.0)  # drain
        time.sleep(0.05)          # refill 5 tokens at 100/s
        assert bucket.try_acquire(4.0) is True

    @pytest.mark.asyncio
    async def test_acquire_blocks_until_refilled(self) -> None:
        # Very high rate so the test completes quickly
        bucket = TokenBucket(rate=1000.0, capacity=5.0)
        bucket.try_acquire(5.0)  # drain all tokens
        start = time.monotonic()
        await bucket.acquire(1.0)
        elapsed = time.monotonic() - start
        # Should have waited ~1ms (1/1000 s) but give generous bounds
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_acquire_exceeds_capacity_raises(self) -> None:
        bucket = TokenBucket(rate=1.0, capacity=5.0)
        with pytest.raises(ValueError, match="capacity"):
            await bucket.acquire(6.0)

    def test_rate_property(self) -> None:
        bucket = TokenBucket(rate=5.0, capacity=10.0)
        assert bucket.rate == 5.0

    def test_capacity_property(self) -> None:
        bucket = TokenBucket(rate=5.0, capacity=10.0)
        assert bucket.capacity == 10.0

    def test_repr(self) -> None:
        bucket = TokenBucket(rate=5.0, capacity=10.0)
        assert "TokenBucket" in repr(bucket)

    def test_invalid_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="rate"):
            TokenBucket(rate=0, capacity=10.0)

    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            TokenBucket(rate=5.0, capacity=0)

    @pytest.mark.asyncio
    async def test_concurrent_acquirers_serialised(self) -> None:
        bucket = TokenBucket(rate=1000.0, capacity=10.0)
        results: list[bool] = []

        async def _task() -> None:
            await bucket.acquire(2.0)
            results.append(True)

        await asyncio.gather(*[_task() for _ in range(5)])
        assert len(results) == 5


# ---------------------------------------------------------------------------
# RateLimiter (multi-bucket registry)
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_add_and_acquire(self) -> None:
        limiter = RateLimiter(default_rate=100.0, default_capacity=50.0)
        limiter.add("api")
        assert limiter.try_acquire("api") is True

    def test_auto_create_on_first_acquire(self) -> None:
        limiter = RateLimiter(default_rate=100.0, default_capacity=10.0)
        # Should not raise even without explicit add()
        assert limiter.try_acquire("implicit") is True

    def test_custom_rate_per_bucket(self) -> None:
        limiter = RateLimiter(default_rate=1.0, default_capacity=10.0)
        limiter.add("fast", rate=100.0, capacity=50.0)
        # Drain the fast bucket
        for _ in range(50):
            limiter.try_acquire("fast")
        # Should still be draining the slow default bucket
        assert limiter.try_acquire("fast") is False

    def test_available(self) -> None:
        limiter = RateLimiter(default_rate=10.0, default_capacity=10.0)
        limiter.add("b")
        avail = limiter.available("b")
        assert avail == pytest.approx(10.0, abs=0.1)

    def test_remove_bucket(self) -> None:
        limiter = RateLimiter()
        limiter.add("to_remove")
        limiter.remove("to_remove")
        assert "to_remove" not in limiter.bucket_names

    def test_remove_nonexistent_noop(self) -> None:
        limiter = RateLimiter()
        limiter.remove("does_not_exist")  # should not raise

    def test_bucket_names_sorted(self) -> None:
        limiter = RateLimiter()
        limiter.add("c")
        limiter.add("a")
        limiter.add("b")
        assert limiter.bucket_names == ["a", "b", "c"]

    def test_repr(self) -> None:
        limiter = RateLimiter()
        limiter.add("test")
        assert "RateLimiter" in repr(limiter)

    @pytest.mark.asyncio
    async def test_async_acquire(self) -> None:
        limiter = RateLimiter(default_rate=1000.0, default_capacity=10.0)
        await limiter.acquire("test_bucket")
        assert limiter.available("test_bucket") < 10.0

    def test_fluent_chaining(self) -> None:
        limiter = RateLimiter()
        result = limiter.add("a").add("b").add("c")
        assert result is limiter
        assert "a" in limiter.bucket_names
