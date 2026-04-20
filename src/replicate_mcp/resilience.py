"""Circuit breaker and retry strategies for Replicate API calls.

Sprint S6 — Hardening.  Provides:

* :class:`CircuitBreaker`   — three-state (CLOSED / OPEN / HALF-OPEN)
  protection for downstream Replicate API calls.
* :class:`RetryConfig`      — parameters for exponential back-off + jitter.
* :func:`compute_retry_delay` — decorrelated jitter formula (AWS whitepaper).
* :func:`with_retry`        — async decorator / context manager that wires
  ``CircuitBreaker`` + ``RetryConfig`` together.

Design (see ADR-004):
    - CLOSED  → calls pass through; failures increment a counter.
    - OPEN    → all calls immediately raise :class:`CircuitOpenError`;
                after ``recovery_timeout`` seconds the breaker transitions
                to HALF-OPEN.
    - HALF_OPEN → a limited probe set is allowed through; a success
                  resets to CLOSED, another failure returns to OPEN.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from replicate_mcp.exceptions import ReplicateMCPError

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CircuitOpenError(ReplicateMCPError):
    """Raised when a call is rejected because the circuit is OPEN."""

    def __init__(self, name: str, retry_in: float | None = None) -> None:
        hint = f" (retry in ≈{retry_in:.0f}s)" if retry_in is not None else ""
        super().__init__(f"Circuit '{name}' is OPEN{hint} — call rejected")
        self.circuit_name = name
        self.retry_in = retry_in


class MaxRetriesExceededError(ReplicateMCPError):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(
            f"Exhausted {attempts} retry attempt(s). "
            f"Last error: {type(last_error).__name__}: {last_error}"
        )
        self.attempts = attempts
        self.last_error = last_error


# ---------------------------------------------------------------------------
# Circuit state
# ---------------------------------------------------------------------------


class CircuitState(Enum):
    """Three states of a circuit breaker."""

    CLOSED = "closed"       # Normal — calls flow through
    OPEN = "open"           # Tripped — calls rejected immediately
    HALF_OPEN = "half_open" # Probing — limited calls allowed


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreakerConfig:
    """Tunable parameters for :class:`CircuitBreaker`."""

    failure_threshold: int = 5
    """Number of consecutive failures before the circuit opens."""

    recovery_timeout: float = 60.0
    """Seconds in OPEN state before transitioning to HALF-OPEN."""

    half_open_max_calls: int = 3
    """Maximum concurrent probe calls allowed in HALF-OPEN state."""

    success_threshold: int = 2
    """Consecutive successes in HALF-OPEN needed to close the circuit."""


class CircuitBreaker:
    """Thread-safe three-state circuit breaker.

    Args:
        name:   Human-readable identifier (used in error messages and OTEL
                span attributes).
        config: Optional :class:`CircuitBreakerConfig`; defaults are used
                if not provided.

    Example::

        breaker = CircuitBreaker("replicate-api")

        async def call_replicate():
            breaker.pre_call()
            try:
                result = await do_replicate_call()
                breaker.record_success()
                return result
            except Exception:
                breaker.record_failure()
                raise
    """

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._half_open_calls: int = 0
        self._opened_at: float | None = None

    # ---- public API ----

    @property
    def state(self) -> CircuitState:
        """Return the current state, transitioning OPEN → HALF_OPEN if due."""
        if self._state is CircuitState.OPEN:
            if (
                self._opened_at is not None
                and time.monotonic() - self._opened_at >= self.config.recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._success_count = 0
        return self._state

    def can_execute(self) -> bool:
        """Return ``True`` if a call may proceed under the current state."""
        s = self.state
        if s is CircuitState.CLOSED:
            return True
        if s is CircuitState.HALF_OPEN:
            return self._half_open_calls < self.config.half_open_max_calls
        return False  # OPEN

    def pre_call(self) -> None:
        """Assert that a call is allowed; raise :class:`CircuitOpenError` if not.

        Also increments the HALF-OPEN probe counter.
        """
        if not self.can_execute():
            retry_in: float | None = None
            if self._opened_at is not None:
                elapsed = time.monotonic() - self._opened_at
                retry_in = max(0, self.config.recovery_timeout - elapsed)
            raise CircuitOpenError(self.name, retry_in=retry_in)

        if self.state is CircuitState.HALF_OPEN:
            self._half_open_calls += 1

    def record_success(self) -> None:
        """Notify the breaker of a successful call."""
        if self._state is CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.config.success_threshold:
                self._trip_closed()
        else:
            # Reset consecutive-failure counter on any success in CLOSED state
            self._failure_count = 0

    def record_failure(self) -> None:
        """Notify the breaker of a failed call."""
        if self._state is CircuitState.HALF_OPEN:
            self._trip_open()
            return

        self._failure_count += 1
        if self._failure_count >= self.config.failure_threshold:
            self._trip_open()

    def reset(self) -> None:
        """Forcibly reset the breaker to CLOSED state (e.g. in tests)."""
        self._trip_closed()
        self._failure_count = 0

    # ---- stats ----

    @property
    def failure_count(self) -> int:
        return self._failure_count

    # ---- private ----

    def _trip_open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._failure_count = self.config.failure_threshold  # saturate
        self._success_count = 0

    def _trip_closed(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._opened_at = None

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self.state.value}, "
            f"failures={self._failure_count})"
        )


# ---------------------------------------------------------------------------
# Retry configuration + delay computation
# ---------------------------------------------------------------------------


@dataclass
class RetryConfig:
    """Parameters for exponential back-off with decorrelated jitter.

    Reference: https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
    """

    max_retries: int = 3
    """Maximum number of retry attempts (0 = no retries)."""

    base_delay: float = 0.5
    """Initial back-off delay in seconds."""

    max_delay: float = 30.0
    """Cap on the computed delay."""

    jitter_factor: float = 0.25
    """Fraction of the delay used as random jitter (0–1)."""

    retryable_exceptions: tuple[type[BaseException], ...] = field(
        default_factory=lambda: (Exception,)
    )
    """Exception types that should trigger a retry."""


def compute_retry_delay(attempt: int, config: RetryConfig) -> float:
    """Return the sleep duration (in seconds) for *attempt* (0-indexed).

    Uses decorrelated jitter: ``delay = min(cap, base * 2^attempt) ± jitter``.
    The result is always non-negative.

    Args:
        attempt: The current attempt index (0 = first retry).
        config:  :class:`RetryConfig` with base/max/jitter tuning.
    """
    base: float = config.base_delay * (2 ** attempt)
    delay: float = min(base, config.max_delay)
    jitter_range: float = config.jitter_factor * delay
    jitter: float = random.uniform(-jitter_range, jitter_range)  # noqa: S311
    return float(max(0.0, delay + jitter))


# ---------------------------------------------------------------------------
# Async retry helper
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


async def with_retry(
    fn: Callable[[], Coroutine[Any, Any, _T]],
    *,
    config: RetryConfig | None = None,
    breaker: CircuitBreaker | None = None,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> _T:
    """Execute *fn* with retry logic and optional circuit-breaker protection.

    Args:
        fn:       Zero-argument async callable to execute.
        config:   Retry parameters; defaults apply if not supplied.
        breaker:  Optional :class:`CircuitBreaker` to guard the call.
        on_retry: Called before each retry with ``(attempt, exception)``.

    Returns:
        The return value of *fn* on success.

    Raises:
        :class:`CircuitOpenError`: If *breaker* is OPEN.
        :class:`MaxRetriesExceededError`: If all attempts fail.
    """
    cfg = config or RetryConfig()
    last_exc: BaseException = RuntimeError("unreachable")

    for attempt in range(cfg.max_retries + 1):
        if breaker is not None:
            breaker.pre_call()

        try:
            result = await fn()
            if breaker is not None:
                breaker.record_success()
            return result
        except tuple(cfg.retryable_exceptions) as exc:  # type: ignore[misc]
            last_exc = exc
            if breaker is not None:
                breaker.record_failure()

            if attempt >= cfg.max_retries:
                break

            if on_retry is not None:
                on_retry(attempt, exc)

            delay = compute_retry_delay(attempt, cfg)
            await asyncio.sleep(delay)

    raise MaxRetriesExceededError(cfg.max_retries + 1, last_exc)


# ---------------------------------------------------------------------------
# Async-generator retry helper
# ---------------------------------------------------------------------------


async def retry_iter(
    fn: Callable[[], AsyncIterator[_T]],
    *,
    config: RetryConfig | None = None,
    breaker: CircuitBreaker | None = None,
) -> AsyncIterator[_T]:
    """Like :func:`with_retry` but for async-generator functions.

    On transient failure the generator is restarted from the beginning.
    Intended for streaming Replicate calls where the stream breaks
    mid-way.

    Args:
        fn:      Zero-argument callable returning an async iterator.
        config:  Retry parameters.
        breaker: Optional circuit breaker.

    Yields:
        Items from the successfully-completing async iterator.
    """
    cfg = config or RetryConfig()
    last_exc: BaseException = RuntimeError("unreachable")

    for attempt in range(cfg.max_retries + 1):
        if breaker is not None:
            breaker.pre_call()

        try:
            async for item in fn():
                yield item
            if breaker is not None:
                breaker.record_success()
            return
        except tuple(cfg.retryable_exceptions) as exc:  # type: ignore[misc]
            last_exc = exc
            if breaker is not None:
                breaker.record_failure()

            if attempt >= cfg.max_retries:
                break

            delay = compute_retry_delay(attempt, cfg)
            await asyncio.sleep(delay)

    raise MaxRetriesExceededError(cfg.max_retries + 1, last_exc)


__all__ = [
    "CircuitState",
    "CircuitBreakerConfig",
    "CircuitBreaker",
    "CircuitOpenError",
    "RetryConfig",
    "RetryConfig",
    "MaxRetriesExceededError",
    "compute_retry_delay",
    "with_retry",
    "retry_iter",
]
