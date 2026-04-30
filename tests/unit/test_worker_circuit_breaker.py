"""Tests for worker circuit breaker integration.

Sprint S17 — Worker Circuit Breakers (v0.8.0)
"""

import pytest
from replicate_mcp.resilience import CircuitBreakerConfig, CircuitState
from replicate_mcp.worker_circuit_breaker import (
    WorkerCircuitBreaker,
    WorkerCircuitOpenError,
    WorkerCircuitState,
)


class TestWorkerCircuitState:
    """Test WorkerCircuitState dataclass."""

    def test_basic_creation(self) -> None:
        """WorkerCircuitState can be created with all fields."""
        state = WorkerCircuitState(
            state="closed",
            failure_count=0,
            success_count=0,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=True,
        )

        assert state.state == "closed"
        assert state.can_execute is True
        assert state.failure_count == 0

    def test_to_dict_serialization(self) -> None:
        """to_dict() returns serializable dictionary."""
        state = WorkerCircuitState(
            state="open",
            failure_count=5,
            success_count=0,
            last_failure_at=1234567890.0,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=False,
        )

        d = state.to_dict()
        assert d["state"] == "open"
        assert d["failure_count"] == 5
        assert d["last_failure_at"] == 1234567890.0
        assert d["can_execute"] is False

    def test_from_circuit_breaker_closed(self) -> None:
        """from_circuit_breaker captures CLOSED state correctly."""
        breaker = WorkerCircuitBreaker("test-breaker")
        state = WorkerCircuitState.from_circuit_breaker(breaker)

        assert state.state == "closed"
        assert state.can_execute is True
        assert state.failure_count == 0
        assert state.success_count == 0

    def test_from_circuit_breaker_with_last_failure(self) -> None:
        """from_circuit_breaker captures last_failure_at if provided."""
        breaker = WorkerCircuitBreaker("test-breaker")
        last_failure = 1234567890.0

        state = WorkerCircuitState.from_circuit_breaker(breaker, last_failure)

        assert state.last_failure_at == last_failure


class TestWorkerCircuitBreaker:
    """Test WorkerCircuitBreaker extension."""

    def test_tracks_last_failure_timestamp(self) -> None:
        """WorkerCircuitBreaker tracks timestamp of last failure."""
        breaker = WorkerCircuitBreaker("test")

        # Initially no failure timestamp
        state1 = breaker.get_state()
        assert state1.last_failure_at is None

        # Record failures to open circuit
        config = breaker.config
        for _ in range(config.failure_threshold + 1):
            try:
                breaker.pre_call()
                breaker.record_failure()
            except Exception:
                pass  # Circuit may open

        # Now should have failure timestamp
        state2 = breaker.get_state()
        assert state2.last_failure_at is not None

    def test_get_state_returns_worker_state(self) -> None:
        """get_state() returns WorkerCircuitState not base CircuitState."""
        breaker = WorkerCircuitBreaker("test")
        state = breaker.get_state()

        assert isinstance(state, WorkerCircuitState)
        assert state.state == "closed"
        assert state.failure_count == 0

    def test_inherits_circuit_breaker_behavior(self) -> None:
        """WorkerCircuitBreaker maintains all base CircuitBreaker functionality."""
        config = CircuitBreakerConfig(failure_threshold=3)
        breaker = WorkerCircuitBreaker("test", config)

        # Record failures to approach threshold
        for i in range(3):
            breaker.pre_call()
            breaker.record_failure()

        # Circuit should be OPEN now
        state = breaker.get_state()
        assert state.state == "open"
        assert state.can_execute is False

        # pre_call should raise
        with pytest.raises(Exception):  # noqa: B017
            breaker.pre_call()

    def test_name_includes_worker_identifier(self) -> None:
        """Breaker name typically includes host/port for identification."""
        breaker = WorkerCircuitBreaker("worker-192.168.1.1:7999")
        assert "192.168.1.1" in breaker.name
        assert "7999" in breaker.name


class TestWorkerCircuitOpenError:
    """Test WorkerCircuitOpenError exception."""

    def test_creation_with_worker_url(self) -> None:
        """Error captures worker URL and circuit state."""
        state = WorkerCircuitState(
            state="open",
            failure_count=5,
            success_count=0,
            last_failure_at=1234567890.0,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=False,
        )

        error = WorkerCircuitOpenError(
            worker_url="http://worker-1:7999",
            circuit_name="worker-worker-1:7999",
            circuit_state=state,
            retry_in=45.5,
        )

        assert "worker-1:7999" in str(error)
        assert "OPEN" in str(error)  # CircuitOpenError uses uppercase
        assert "retry in" in str(error)  # Check that retry_in is included
        assert error.worker_url == "http://worker-1:7999"
        assert error.circuit_state == state
        assert error.retry_in == 45.5

    def test_message_without_retry_in(self) -> None:
        """Message is concise when retry_in is None."""
        state = WorkerCircuitState(
            state="open",
            failure_count=5,
            success_count=0,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=False,
        )

        error = WorkerCircuitOpenError(
            worker_url="http://worker-1:7999",
            circuit_name="worker-worker-1:7999",
            circuit_state=state,
            retry_in=None,
        )

        assert "worker-1:7999" in str(error)
        assert "OPEN" in str(error)
        assert "retry in" not in str(error)

    def test_inherits_circuit_open_error(self) -> None:
        """WorkerCircuitOpenError is a subclass for catching."""
        from replicate_mcp.resilience import CircuitOpenError

        state = WorkerCircuitState(
            state="open",
            failure_count=5,
            success_count=0,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=False,
        )

        error = WorkerCircuitOpenError(
            worker_url="http://worker-1:7999",
            circuit_name="worker-worker-1:7999",
            circuit_state=state,
        )

        assert isinstance(error, CircuitOpenError)


class TestCircuitBreakerConfigPropagation:
    """Test that WorkerCircuitBreaker respects configuration."""

    def test_custom_recovery_timeout(self) -> None:
        """Custom recovery timeout is respected in state."""
        config = CircuitBreakerConfig(recovery_timeout=120.0)
        breaker = WorkerCircuitBreaker("test", config)

        state = breaker.get_state()
        assert state.recovery_timeout == 120.0

    def test_custom_half_open_max_calls(self) -> None:
        """Custom half_open_max_calls is respected in state."""
        config = CircuitBreakerConfig(half_open_max_calls=5)
        breaker = WorkerCircuitBreaker("test", config)

        state = breaker.get_state()
        assert state.half_open_max_calls == 5

    def test_custom_failure_threshold(self) -> None:
        """Custom failure_threshold affects when circuit opens."""
        config = CircuitBreakerConfig(failure_threshold=2)
        breaker = WorkerCircuitBreaker("test", config)

        # Should open after 2 failures
        breaker.pre_call()
        breaker.record_failure()
        breaker.pre_call()
        breaker.record_failure()

        # Circuit should now be OPEN
        state = breaker.get_state()
        assert state.state == "open"
