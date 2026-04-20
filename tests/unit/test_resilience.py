"""Tests for replicate_mcp.resilience — circuit breaker and retry logic."""

from __future__ import annotations

import time

import pytest

from replicate_mcp.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    MaxRetriesExceededError,
    RetryConfig,
    compute_retry_delay,
    retry_iter,
    with_retry,
)

# ---------------------------------------------------------------------------
# CircuitBreaker — state transitions
# ---------------------------------------------------------------------------


class TestCircuitBreakerStates:
    def _make_breaker(self, threshold: int = 3, recovery: float = 60.0) -> CircuitBreaker:
        cfg = CircuitBreakerConfig(
            failure_threshold=threshold,
            recovery_timeout=recovery,
            half_open_max_calls=2,
            success_threshold=2,
        )
        return CircuitBreaker("test", cfg)

    def test_initial_state_closed(self) -> None:
        b = self._make_breaker()
        assert b.state is CircuitState.CLOSED

    def test_can_execute_when_closed(self) -> None:
        b = self._make_breaker()
        assert b.can_execute() is True

    def test_trips_open_after_threshold_failures(self) -> None:
        b = self._make_breaker(threshold=3)
        for _ in range(3):
            b.record_failure()
        assert b.state is CircuitState.OPEN

    def test_open_rejects_calls(self) -> None:
        b = self._make_breaker(threshold=1)
        b.record_failure()
        assert b.can_execute() is False

    def test_pre_call_raises_when_open(self) -> None:
        b = self._make_breaker(threshold=1)
        b.record_failure()
        with pytest.raises(CircuitOpenError):
            b.pre_call()

    def test_circuit_open_error_has_name(self) -> None:
        b = self._make_breaker(threshold=1)
        b.record_failure()
        with pytest.raises(CircuitOpenError) as exc_info:
            b.pre_call()
        assert exc_info.value.circuit_name == "test"

    def test_success_resets_failure_count(self) -> None:
        b = self._make_breaker(threshold=3)
        b.record_failure()
        b.record_failure()
        b.record_success()
        assert b.failure_count == 0

    def test_half_open_after_recovery_timeout(self) -> None:
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01)
        b = CircuitBreaker("test", cfg)
        b.record_failure()
        assert b.state is CircuitState.OPEN
        time.sleep(0.02)
        # Phase 4: state is a pure getter; recovery is triggered by can_execute()
        b.can_execute()  # triggers _maybe_recover() → HALF_OPEN
        assert b.state is CircuitState.HALF_OPEN

    def test_half_open_allows_limited_calls(self) -> None:
        cfg = CircuitBreakerConfig(
            failure_threshold=1, recovery_timeout=0.01, half_open_max_calls=2
        )
        b = CircuitBreaker("test", cfg)
        b.record_failure()
        time.sleep(0.02)
        # Should allow up to half_open_max_calls
        assert b.can_execute() is True
        b.pre_call()
        assert b.can_execute() is True
        b.pre_call()
        assert b.can_execute() is False  # exceeded probe limit

    def test_half_open_closes_after_successes(self) -> None:
        cfg = CircuitBreakerConfig(
            failure_threshold=1, recovery_timeout=0.01,
            half_open_max_calls=3, success_threshold=2
        )
        b = CircuitBreaker("test", cfg)
        b.record_failure()
        time.sleep(0.02)
        # Phase 4: recovery triggered by can_execute(); state is then a pure read
        b.can_execute()  # triggers _maybe_recover() → HALF_OPEN
        assert b.state is CircuitState.HALF_OPEN
        b.record_success()
        b.record_success()
        assert b.state is CircuitState.CLOSED

    def test_half_open_reopens_on_failure(self) -> None:
        cfg = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01)
        b = CircuitBreaker("test", cfg)
        b.record_failure()
        time.sleep(0.02)
        b.can_execute()  # triggers _maybe_recover() → HALF_OPEN
        assert b.state is CircuitState.HALF_OPEN
        b.record_failure()
        assert b.state is CircuitState.OPEN

    def test_reset_clears_state(self) -> None:
        b = self._make_breaker(threshold=1)
        b.record_failure()
        assert b.state is CircuitState.OPEN
        b.reset()
        assert b.state is CircuitState.CLOSED
        assert b.failure_count == 0

    def test_repr(self) -> None:
        b = self._make_breaker()
        assert "CircuitBreaker" in repr(b)
        assert "closed" in repr(b)


# ---------------------------------------------------------------------------
# RetryConfig and compute_retry_delay
# ---------------------------------------------------------------------------


class TestRetryDelay:
    def test_delay_increases_with_attempts(self) -> None:
        cfg = RetryConfig(base_delay=1.0, max_delay=60.0, jitter_factor=0.0)
        d0 = compute_retry_delay(0, cfg)
        d1 = compute_retry_delay(1, cfg)
        d2 = compute_retry_delay(2, cfg)
        assert d0 < d1 < d2

    def test_delay_capped_at_max(self) -> None:
        cfg = RetryConfig(base_delay=1.0, max_delay=5.0, jitter_factor=0.0)
        for attempt in range(10):
            assert compute_retry_delay(attempt, cfg) <= 5.0

    def test_delay_never_negative(self) -> None:
        cfg = RetryConfig(base_delay=0.1, max_delay=10.0, jitter_factor=0.99)
        for attempt in range(5):
            assert compute_retry_delay(attempt, cfg) >= 0.0


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------


class TestWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self) -> None:
        calls: list[int] = []

        async def fn() -> str:
            calls.append(1)
            return "ok"

        result = await with_retry(fn)
        assert result == "ok"
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self) -> None:
        attempts: list[int] = []

        async def fn() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise ValueError("transient error")
            return "recovered"

        cfg = RetryConfig(max_retries=3, base_delay=0, jitter_factor=0)
        result = await with_retry(fn, config=cfg)
        assert result == "recovered"
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_raises_max_retries_exceeded(self) -> None:
        async def fn() -> str:
            raise RuntimeError("always fails")

        cfg = RetryConfig(max_retries=2, base_delay=0, jitter_factor=0)
        with pytest.raises(MaxRetriesExceededError) as exc_info:
            await with_retry(fn, config=cfg)
        assert exc_info.value.attempts == 3  # 1 original + 2 retries

    @pytest.mark.asyncio
    async def test_circuit_breaker_rejection(self) -> None:
        breaker = CircuitBreaker("x", CircuitBreakerConfig(failure_threshold=1))
        breaker.record_failure()

        async def fn() -> str:
            return "should not be reached"

        with pytest.raises(CircuitOpenError):
            await with_retry(fn, breaker=breaker)

    @pytest.mark.asyncio
    async def test_on_retry_callback_called(self) -> None:
        retries: list[int] = []

        async def fn() -> str:
            if len(retries) < 1:
                raise ValueError("first try fails")
            return "ok"

        def on_retry(attempt: int, exc: BaseException) -> None:
            retries.append(attempt)

        cfg = RetryConfig(max_retries=2, base_delay=0, jitter_factor=0)
        await with_retry(fn, config=cfg, on_retry=on_retry)
        assert len(retries) == 1

    @pytest.mark.asyncio
    async def test_circuit_records_success(self) -> None:
        breaker = CircuitBreaker(
            "x", CircuitBreakerConfig(failure_threshold=5)
        )

        async def fn() -> str:
            return "ok"

        await with_retry(fn, breaker=breaker)
        assert breaker.state is CircuitState.CLOSED


# ---------------------------------------------------------------------------
# retry_iter
# ---------------------------------------------------------------------------


class TestRetryIter:
    @pytest.mark.asyncio
    async def test_yields_all_items_on_success(self) -> None:
        async def _gen():
            for i in range(3):
                yield i

        items = []
        async for item in retry_iter(_gen):
            items.append(item)
        assert items == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self) -> None:
        call_count = [0]

        async def _gen():
            call_count[0] += 1
            if call_count[0] < 2:
                raise RuntimeError("transient")
            yield "good"

        items = []
        cfg = RetryConfig(max_retries=2, base_delay=0, jitter_factor=0)
        async for item in retry_iter(_gen, config=cfg):
            items.append(item)
        assert items == ["good"]

    @pytest.mark.asyncio
    async def test_max_retries_exceeded_raises(self) -> None:
        async def _gen():
            raise RuntimeError("always fails")
            yield  # make it a generator

        cfg = RetryConfig(max_retries=1, base_delay=0, jitter_factor=0)
        with pytest.raises(MaxRetriesExceededError):
            async for _ in retry_iter(_gen, config=cfg):
                pass
