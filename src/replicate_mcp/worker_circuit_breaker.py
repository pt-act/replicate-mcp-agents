"""Worker circuit breaker integration for distributed execution.

Sprint S17 — Production Hardening. Extends the core :class:`CircuitBreaker`
to HTTP worker nodes so coordinators can track worker health and automatically
failover when workers become unreliable.

Key Components:

* :class:`WorkerCircuitState` — Serializable circuit state exposed via
  the worker health endpoint.
* :class:`WorkerCircuitBreaker` — Circuit breaker with automatic metrics
  integration for HTTP workers.
* :class:`WorkerCircuitOpenError` — Raised when routing to a worker
  with an OPEN circuit.

Design:

- Workers self-report their circuit state via the /health endpoint.
- Coordinators check circuit state before routing and respect HALF_OPEN
  probe limits to avoid overwhelming recovering workers.
- Circuit state transitions are deterministic and observable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from replicate_mcp.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
)


@dataclass
class WorkerCircuitState:
    """Serializable circuit breaker state for worker health endpoints.

    This dataclass is designed to be JSON-serializable and exposed
    via the worker's /health endpoint so coordinators can make
    intelligent routing decisions.

    Attributes:
        state: Current circuit state (CLOSED, OPEN, HALF_OPEN).
        failure_count: Consecutive failures in current window.
        success_count: Consecutive successes in HALF_OPEN state.
        last_failure_at: Unix timestamp of last failure (or None).
        recovery_timeout: Seconds before OPEN → HALF_OPEN transition.
        half_open_max_calls: Max concurrent probes in HALF_OPEN state.
        half_open_calls: Current number of active probe calls.
        can_execute: Whether new calls should be accepted.
    """

    state: str  # "closed", "open", "half_open"
    failure_count: int
    success_count: int
    last_failure_at: float | None
    recovery_timeout: float
    half_open_max_calls: int
    half_open_calls: int
    can_execute: bool

    @classmethod
    def from_circuit_breaker(
        cls,
        breaker: CircuitBreaker,
        last_failure_at: float | None = None,
    ) -> WorkerCircuitState:
        """Create a WorkerCircuitState from a CircuitBreaker instance.

        This method captures the current state of a circuit breaker
        in a form suitable for JSON serialization and health endpoint
        responses.

        Args:
            breaker: The circuit breaker to snapshot.
            last_failure_at: Optional timestamp of the most recent failure
                (CircuitBreaker doesn't track this internally).

        Returns:
            A serializable state snapshot.
        """
        # Get the internal state (may trigger recovery check)
        circuit_state = breaker.state

        # Access internal counters (these are implementation details but
        # necessary for accurate health reporting)
        failure_count = getattr(breaker, '_failure_count', 0)
        success_count = getattr(breaker, '_success_count', 0)
        half_open_calls = getattr(breaker, '_half_open_calls', 0)

        # Check if execution is currently allowed
        can_execute = breaker.can_execute()

        return cls(
            state=circuit_state.value.lower(),
            failure_count=failure_count,
            success_count=success_count,
            last_failure_at=last_failure_at,
            recovery_timeout=breaker.config.recovery_timeout,
            half_open_max_calls=breaker.config.half_open_max_calls,
            half_open_calls=half_open_calls,
            can_execute=can_execute,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dictionary for JSON serialization."""
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_at": self.last_failure_at,
            "recovery_timeout": self.recovery_timeout,
            "half_open_max_calls": self.half_open_max_calls,
            "half_open_calls": self.half_open_calls,
            "can_execute": self.can_execute,
        }


class WorkerCircuitBreaker(CircuitBreaker):
    """Circuit breaker with worker-specific tracking and health integration.

    Extends the base CircuitBreaker to track the timestamp of the most
    recent failure, which is useful for health endpoint reporting and
    coordinator decision-making.

    Args:
        name: Human-readable identifier (typically includes host/port).
        config: Optional CircuitBreakerConfig; defaults used if not provided.

    Example::

        breaker = WorkerCircuitBreaker(f"worker-{host}:{port}")

        # In health endpoint handler:
        state = WorkerCircuitState.from_circuit_breaker(breaker)
        return JSONResponse({"circuit": state.to_dict()})

        # In execution handler:
        breaker.pre_call()  # Raises CircuitOpenError if circuit is OPEN
        try:
            result = await execute_task()
            breaker.record_success()
        except Exception:
            breaker.record_failure()
            raise
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        super().__init__(name, config)
        self._last_failure_at: float | None = None

    def record_failure(self) -> None:
        """Notify the breaker of a failed call, tracking failure timestamp."""
        import time

        self._last_failure_at = time.monotonic()
        super().record_failure()

    def get_state(self) -> WorkerCircuitState:
        """Get current circuit state as a serializable snapshot."""
        return WorkerCircuitState.from_circuit_breaker(self, self._last_failure_at)


class WorkerCircuitOpenError(CircuitOpenError):
    """Raised when attempting to route to a worker with an OPEN circuit.

    This exception extends CircuitOpenError to provide worker-specific
    context for coordinator-side routing decisions.

    Attributes:
        worker_url: The URL of the worker that was unavailable.
        circuit_state: The circuit state snapshot at the time of rejection.
    """

    def __init__(
        self,
        worker_url: str,
        circuit_name: str,
        circuit_state: WorkerCircuitState,
        retry_in: float | None = None,
    ) -> None:
        message = f"Worker {worker_url} circuit is {circuit_state.state}"
        if retry_in is not None:
            message += f" (retry in {retry_in:.1f}s)"

        super().__init__(circuit_name, retry_in=retry_in)
        self.worker_url = worker_url
        self.circuit_state = circuit_state
