"""Tests for replicate_mcp.interfaces — Protocol ABC conformance checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

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
    async def run(self, agent_id: str, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        async def _gen() -> AsyncIterator[dict[str, Any]]:
            yield {"done": True}

        return _gen()


class _FakeRegistry:
    def register(self, agent: Any) -> None: ...
    def get(self, safe_name: str) -> Any:
        return None

    def has(self, safe_name: str) -> bool:
        return False

    def list_agents(self) -> dict:
        return {}

    @property
    def count(self) -> int:
        return 0


class _FakeCheckpoint:
    def save(self, session_id: str, state: dict) -> Path:
        return Path("/tmp/ckpt")  # noqa: S108

    def load(self, session_id: str) -> dict:
        return {}

    def exists(self, session_id: str) -> bool:
        return False

    def delete(self, session_id: str) -> None: ...
    def list_sessions(self) -> list:
        return []


class _FakeTelemetry:
    def record(self, event: Any) -> None: ...
    def total_cost(self) -> float:
        return 0.0

    def average_latency(self) -> float:
        return 0.0

    @property
    def events(self) -> list:
        return []


class _FakeRouter:
    def register_model(self, model: str, **kwargs: Any) -> None: ...
    def select_model(self, candidates: list[str]) -> str:
        return candidates[0] if candidates else ""

    def record_outcome(
        self,
        model: str,
        *,
        latency_ms: float,
        cost_usd: float,
        success: bool = True,
        quality: float = 1.0,
    ) -> None: ...


class _FakeBreaker:
    def can_execute(self) -> bool:
        return True

    def record_success(self) -> None: ...
    def record_failure(self) -> None: ...


class _FakeLimiter:
    async def acquire(self, tokens: float = 1.0) -> None: ...
    def try_acquire(self, tokens: float = 1.0) -> bool:
        return True


class _FakeObs:
    def record_invocation(
        self, model: str, latency_ms: float, cost_usd: float, success: bool
    ) -> None: ...
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


# ---------------------------------------------------------------------------
# Protocol stub execution — cover the ``...`` bodies in interfaces.py
#
# Protocols cannot be instantiated directly (TypeError), so we call
# each stub method as an unbound function with a bare ``_Dummy`` self.
# ---------------------------------------------------------------------------


class _Dummy:
    """Bare object used as ``self`` for unbound Protocol method calls."""


# -- AgentExecutorProtocol ---------------------------------------------------


class TestAgentExecutorProtocolStubs:
    @pytest.mark.asyncio
    async def test_run_stub(self) -> None:
        result = await AgentExecutorProtocol.run(_Dummy(), "agent-1", {"prompt": "hi"})
        assert result is None


# -- AgentRegistryProtocol ---------------------------------------------------


class TestAgentRegistryProtocolStubs:
    def test_register_stub(self) -> None:
        assert AgentRegistryProtocol.register(_Dummy(), "agent") is None

    def test_get_stub(self) -> None:
        assert AgentRegistryProtocol.get(_Dummy(), "agent") is None

    def test_has_stub(self) -> None:
        assert AgentRegistryProtocol.has(_Dummy(), "agent") is None

    def test_list_agents_stub(self) -> None:
        assert AgentRegistryProtocol.list_agents(_Dummy()) is None

    def test_count_stub(self) -> None:
        # Properties: call the fget directly on a dummy instance
        assert AgentRegistryProtocol.count.fget(_Dummy()) is None  # type: ignore[attr-defined]


# -- CheckpointManagerProtocol -----------------------------------------------


class TestCheckpointManagerProtocolStubs:
    def test_save_stub(self) -> None:
        assert CheckpointManagerProtocol.save(_Dummy(), "sid", {}) is None

    def test_load_stub(self) -> None:
        assert CheckpointManagerProtocol.load(_Dummy(), "sid") is None

    def test_exists_stub(self) -> None:
        assert CheckpointManagerProtocol.exists(_Dummy(), "sid") is None

    def test_delete_stub(self) -> None:
        assert CheckpointManagerProtocol.delete(_Dummy(), "sid") is None

    def test_list_sessions_stub(self) -> None:
        assert CheckpointManagerProtocol.list_sessions(_Dummy()) is None


# -- TelemetryTrackerProtocol ------------------------------------------------


class TestTelemetryTrackerProtocolStubs:
    def test_record_stub(self) -> None:
        assert TelemetryTrackerProtocol.record(_Dummy(), "event") is None

    def test_total_cost_stub(self) -> None:
        assert TelemetryTrackerProtocol.total_cost(_Dummy()) is None

    def test_average_latency_stub(self) -> None:
        assert TelemetryTrackerProtocol.average_latency(_Dummy()) is None

    def test_events_stub(self) -> None:
        assert TelemetryTrackerProtocol.events.fget(_Dummy()) is None  # type: ignore[attr-defined]


# -- ModelRouterProtocol -----------------------------------------------------


class TestModelRouterProtocolStubs:
    def test_register_model_stub(self) -> None:
        assert ModelRouterProtocol.register_model(_Dummy(), "model-a") is None

    def test_select_model_stub(self) -> None:
        assert ModelRouterProtocol.select_model(_Dummy(), ["model-a"]) is None

    def test_record_outcome_stub(self) -> None:
        assert (
            ModelRouterProtocol.record_outcome(
                _Dummy(), "model-a", latency_ms=100.0, cost_usd=0.01, success=True, quality=1.0
            )
            is None
        )


# -- CircuitBreakerProtocol --------------------------------------------------


class TestCircuitBreakerProtocolStubs:
    def test_can_execute_stub(self) -> None:
        assert CircuitBreakerProtocol.can_execute(_Dummy()) is None

    def test_record_success_stub(self) -> None:
        assert CircuitBreakerProtocol.record_success(_Dummy()) is None

    def test_record_failure_stub(self) -> None:
        assert CircuitBreakerProtocol.record_failure(_Dummy()) is None


# -- RateLimiterProtocol -----------------------------------------------------


class TestRateLimiterProtocolStubs:
    @pytest.mark.asyncio
    async def test_acquire_stub(self) -> None:
        result = await RateLimiterProtocol.acquire(_Dummy(), 1.0)
        assert result is None

    def test_try_acquire_stub(self) -> None:
        assert RateLimiterProtocol.try_acquire(_Dummy(), 1.0) is None


# -- ObservabilityProtocol ---------------------------------------------------


class TestObservabilityProtocolStubs:
    def test_record_invocation_stub(self) -> None:
        assert ObservabilityProtocol.record_invocation(_Dummy(), "model", 100.0, 0.01, True) is None

    def test_increment_counter_stub(self) -> None:
        assert (
            ObservabilityProtocol.increment_counter(_Dummy(), "metric", value=1, label="test")
            is None
        )
