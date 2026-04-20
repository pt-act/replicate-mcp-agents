"""Abstract Protocol interfaces for all Replicate MCP subsystems.

Sprint S5 — Hardening.  Every concrete implementation (AgentExecutor,
AgentRegistry, CheckpointManager, …) now satisfies one of these
``typing.Protocol`` contracts.  This decouples consumers from concrete
types, making it trivial to swap implementations in tests or extend the
system with alternative backends.

Usage::

    from replicate_mcp.interfaces import (
        AgentExecutorProtocol,
        AgentRegistryProtocol,
        CheckpointManagerProtocol,
        TelemetryTrackerProtocol,
        ModelRouterProtocol,
    )

    def build_service(executor: AgentExecutorProtocol) -> None:
        ...
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentExecutorProtocol(Protocol):
    """Contract for executing a single agent against a payload."""

    async def run(
        self,
        agent_id: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute *agent_id* with *payload*, yielding output chunks."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentRegistryProtocol(Protocol):
    """Contract for a registry that stores agent metadata."""

    def register(self, agent: Any) -> None:
        """Register a new agent, raising on duplicates."""
        ...

    def get(self, safe_name: str) -> Any:
        """Return metadata for *safe_name*, raising if absent."""
        ...

    def has(self, safe_name: str) -> bool:
        """Return ``True`` if *safe_name* is registered."""
        ...

    def list_agents(self) -> dict[str, Any]:
        """Return a snapshot of all registered agents."""
        ...

    @property
    def count(self) -> int:
        """Number of currently registered agents."""
        ...


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


@runtime_checkable
class CheckpointManagerProtocol(Protocol):
    """Contract for persisting and loading workflow checkpoints."""

    def save(self, session_id: str, state: dict[str, Any]) -> Path:
        """Persist *state* under *session_id* and return the file path."""
        ...

    def load(self, session_id: str) -> dict[str, Any]:
        """Load and return the state for *session_id*."""
        ...

    def exists(self, session_id: str) -> bool:
        """Return ``True`` if a checkpoint for *session_id* exists."""
        ...

    def delete(self, session_id: str) -> None:
        """Delete the checkpoint for *session_id*."""
        ...

    def list_sessions(self) -> list[str]:
        """Return sorted list of all saved session IDs."""
        ...


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


@runtime_checkable
class TelemetryTrackerProtocol(Protocol):
    """Contract for recording and querying telemetry events."""

    def record(self, event: Any) -> None:
        """Append a telemetry event."""
        ...

    def total_cost(self) -> float:
        """Return the sum of all recorded costs in USD."""
        ...

    def average_latency(self) -> float:
        """Return the mean latency in milliseconds across all events."""
        ...

    @property
    def events(self) -> list[Any]:
        """Return a copy of all recorded events."""
        ...


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@runtime_checkable
class ModelRouterProtocol(Protocol):
    """Contract for selecting the best model from a candidate set."""

    def register_model(self, model: str, **kwargs: Any) -> None:
        """Register *model* with optional initial statistics."""
        ...

    def select_model(self, candidates: list[str]) -> str:
        """Return the preferred model from *candidates*."""
        ...

    def record_outcome(
        self,
        model: str,
        *,
        latency_ms: float,
        cost_usd: float,
        success: bool = True,
        quality: float = 1.0,
    ) -> None:
        """Update model statistics after an invocation."""
        ...


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


@runtime_checkable
class CircuitBreakerProtocol(Protocol):
    """Contract for a circuit breaker protecting a downstream call."""

    def can_execute(self) -> bool:
        """Return ``True`` if the circuit allows execution."""
        ...

    def record_success(self) -> None:
        """Notify the breaker that an execution succeeded."""
        ...

    def record_failure(self) -> None:
        """Notify the breaker that an execution failed."""
        ...


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


@runtime_checkable
class RateLimiterProtocol(Protocol):
    """Contract for an async rate limiter."""

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until *tokens* tokens are available."""
        ...

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking attempt to acquire *tokens* tokens."""
        ...


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


@runtime_checkable
class ObservabilityProtocol(Protocol):
    """Contract for recording traces and metrics."""

    def record_invocation(
        self,
        model: str,
        latency_ms: float,
        cost_usd: float,
        success: bool,
    ) -> None:
        """Record a single agent invocation in the metrics backend."""
        ...

    def increment_counter(
        self,
        name: str,
        value: int = 1,
        **labels: str,
    ) -> None:
        """Increment a named counter by *value*."""
        ...


__all__ = [
    "AgentExecutorProtocol",
    "AgentRegistryProtocol",
    "CheckpointManagerProtocol",
    "TelemetryTrackerProtocol",
    "ModelRouterProtocol",
    "CircuitBreakerProtocol",
    "RateLimiterProtocol",
    "ObservabilityProtocol",
]
