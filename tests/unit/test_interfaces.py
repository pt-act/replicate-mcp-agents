"""Tests for replicate_mcp.interfaces — Protocol ABC conformance checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from replicate_mcp.interfaces import (
    AgentExecutorProtocol,
    AgentRegistryProtocol,
    CheckpointManagerProtocol,
    CircuitBreakerProtocol,
    ModelRouterProtocol,
    ObservabilityProtocol,
    RateLimiterProtocol,
    TelemetryTrackerProtocol,
)

# ---------------------------------------------------------------------------
# Helpers — minimal concrete implementations for runtime_checkable checks
# ---------------------------------------------------------------------------


class _FakeExecutor:
    async def run(self, agent_id: str, payload: dict) -> AsyncIterator[dict]:
        async def _gen():
            yield {"done": True}
        return _gen()


class _FakeRegistry:
    def register(self, agent: Any) -> None: ...
    def get(self, safe_name: str) -> Any: return None
    def has(self, safe_name: str) -> bool: return False
    def list_agents(self) -> dict: return {}
    @property
    def count(self) -> int: return 0


class _FakeCheckpoint:
    def save(self, session_id: str, state: dict) -> Path:
        return Path("/tmp/ckpt")  # noqa: S108
    def load(self, session_id: str) -> dict: return {}
    def exists(self, session_id: str) -> bool: return False
    def delete(self, session_id: str) -> None: ...
    def list_sessions(self) -> list: return []


class _FakeTelemetry:
    def record(self, event: Any) -> None: ...
    def total_cost(self) -> float: return 0.0
    def average_latency(self) -> float: return 0.0
    @property
    def events(self) -> list: return []


class _FakeRouter:
    def register_model(self, model: str, **kwargs: Any) -> None: ...
    def select_model(self, candidates: list) -> str:
        return candidates[0] if candidates else ""
    def record_outcome(self, model: str, *, latency_ms: float, cost_usd: float,
                       success: bool = True, quality: float = 1.0) -> None: ...


class _FakeBreaker:
    def can_execute(self) -> bool: return True
    def record_success(self) -> None: ...
    def record_failure(self) -> None: ...


class _FakeLimiter:
    async def acquire(self, tokens: float = 1.0) -> None: ...
    def try_acquire(self, tokens: float = 1.0) -> bool: return True


class _FakeObs:
    def record_invocation(self, model: str, latency_ms: float,
                           cost_usd: float, success: bool) -> None: ...
    def increment_counter(self, name: str, value: int = 1, **labels: str) -> None: ...


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Verify that fake implementations satisfy each Protocol at runtime."""

    def test_executor_protocol(self) -> None:
        assert isinstance(_FakeExecutor(), AgentExecutorProtocol)

    def test_registry_protocol(self) -> None:
        assert isinstance(_FakeRegistry(), AgentRegistryProtocol)

    def test_checkpoint_protocol(self) -> None:
        assert isinstance(_FakeCheckpoint(), CheckpointManagerProtocol)

    def test_telemetry_protocol(self) -> None:
        assert isinstance(_FakeTelemetry(), TelemetryTrackerProtocol)

    def test_router_protocol(self) -> None:
        assert isinstance(_FakeRouter(), ModelRouterProtocol)

    def test_breaker_protocol(self) -> None:
        assert isinstance(_FakeBreaker(), CircuitBreakerProtocol)

    def test_rate_limiter_protocol(self) -> None:
        assert isinstance(_FakeLimiter(), RateLimiterProtocol)

    def test_observability_protocol(self) -> None:
        assert isinstance(_FakeObs(), ObservabilityProtocol)


class TestConcreteImplementationsConform:
    """Verify that Phase 2 concrete classes satisfy the protocols."""

    def test_circuit_breaker_conforms(self) -> None:
        from replicate_mcp.resilience import CircuitBreaker
        assert isinstance(CircuitBreaker("test"), CircuitBreakerProtocol)

    def test_token_bucket_conforms(self) -> None:
        from replicate_mcp.ratelimit import TokenBucket
        assert isinstance(TokenBucket(rate=5.0, capacity=10.0), RateLimiterProtocol)

    def test_observability_conforms(self) -> None:
        from replicate_mcp.observability import Observability
        assert isinstance(Observability(), ObservabilityProtocol)

    def test_router_conforms(self) -> None:
        from replicate_mcp.routing import CostAwareRouter
        assert isinstance(CostAwareRouter(), ModelRouterProtocol)

    def test_checkpoint_manager_conforms(self) -> None:
        import tempfile

        from replicate_mcp.utils.checkpointing import CheckpointManager
        with tempfile.TemporaryDirectory() as d:
            assert isinstance(CheckpointManager(Path(d)), CheckpointManagerProtocol)

    def test_agent_registry_conforms(self) -> None:
        from replicate_mcp.agents.registry import AgentRegistry
        assert isinstance(AgentRegistry(), AgentRegistryProtocol)

    def test_telemetry_tracker_conforms(self) -> None:
        from replicate_mcp.utils.telemetry import TelemetryTracker
        assert isinstance(TelemetryTracker(), TelemetryTrackerProtocol)


class TestProtocolsNotConforming:
    """Ensure plain objects that DON'T satisfy the protocol fail isinstance."""

    def test_plain_object_not_executor(self) -> None:
        class _Bad:
            pass
        assert not isinstance(_Bad(), AgentExecutorProtocol)

    def test_plain_object_not_registry(self) -> None:
        class _Bad:
            pass
        assert not isinstance(_Bad(), AgentRegistryProtocol)
