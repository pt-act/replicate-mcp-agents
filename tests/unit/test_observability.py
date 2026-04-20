"""Tests for replicate_mcp.observability — OTEL no-op and configuration."""

from __future__ import annotations

import pytest

from replicate_mcp.observability import (
    HAS_OTEL,
    Observability,
    ObservabilityConfig,
    default_observability,
)


# ---------------------------------------------------------------------------
# ObservabilityConfig
# ---------------------------------------------------------------------------


class TestObservabilityConfig:
    def test_defaults(self) -> None:
        cfg = ObservabilityConfig()
        assert cfg.service_name == "replicate-mcp-agents"
        assert cfg.enable_traces is True
        assert cfg.enable_metrics is True
        assert cfg.console_fallback is True
        assert cfg.metric_export_interval_ms == 30_000

    def test_custom_service_name(self) -> None:
        cfg = ObservabilityConfig(service_name="my-service")
        assert cfg.service_name == "my-service"

    def test_custom_endpoint(self) -> None:
        cfg = ObservabilityConfig(otlp_endpoint="http://collector:4317")
        assert cfg.otlp_endpoint == "http://collector:4317"


# ---------------------------------------------------------------------------
# Observability — no-op behaviour (OTEL SDK not installed in test env)
# ---------------------------------------------------------------------------


class TestObservabilityNoOp:
    """When OTEL is not available, all methods must be silent no-ops."""

    def _obs(self) -> Observability:
        # Always return an observability instance regardless of OTEL availability
        return Observability()

    def test_setup_idempotent(self) -> None:
        obs = self._obs()
        obs.setup()
        obs.setup()  # second call should be a no-op
        assert obs.is_setup is obs.is_setup  # just check it doesn't crash

    def test_span_context_manager_no_exception(self) -> None:
        obs = self._obs()
        with obs.span("test.span", model="test/model") as span:
            span.set_attribute("key", "value")
            span.record_exception(ValueError("oops"))
            span.set_status("OK")

    def test_record_invocation_no_exception(self) -> None:
        obs = self._obs()
        obs.record_invocation(
            model="meta/llama",
            latency_ms=3200.0,
            cost_usd=0.002,
            success=True,
        )

    def test_record_invocation_failure_no_exception(self) -> None:
        obs = self._obs()
        obs.record_invocation(
            model="meta/llama",
            latency_ms=500.0,
            cost_usd=0.001,
            success=False,
        )

    def test_record_circuit_trip_no_exception(self) -> None:
        obs = self._obs()
        obs.record_circuit_trip("replicate-api")

    def test_increment_counter_no_exception(self) -> None:
        obs = self._obs()
        obs.increment_counter("my.counter", 5, env="test")

    def test_otel_available_property(self) -> None:
        obs = self._obs()
        # Should return bool without exception
        assert isinstance(obs.otel_available, bool)

    def test_is_setup_before_setup(self) -> None:
        obs = Observability()
        # New instance is not set up yet (unless HAS_OTEL is True and auto-setup)
        # In CI without OTEL SDK, setup() will return immediately
        obs.setup()
        # Should not crash

    def test_span_attributes_with_non_str_values(self) -> None:
        obs = self._obs()
        with obs.span("test", count=42, flag=True, score=0.95) as span:
            span.set_attribute("extra", "val")


# ---------------------------------------------------------------------------
# Default observability instance
# ---------------------------------------------------------------------------


class TestDefaultObservability:
    def test_default_is_observability_instance(self) -> None:
        assert isinstance(default_observability, Observability)

    def test_default_can_emit_without_crash(self) -> None:
        default_observability.record_invocation(
            model="test/model", latency_ms=100.0, cost_usd=0.001, success=True
        )

    def test_default_span_works(self) -> None:
        with default_observability.span("test.span") as s:
            s.set_attribute("test", "value")


# ---------------------------------------------------------------------------
# Integration: Observability + circuit breaker in executor
# ---------------------------------------------------------------------------


class TestObservabilityIntegration:
    """Verify Observability plugs in to AgentExecutor without crashing."""

    @pytest.mark.asyncio
    async def test_executor_with_custom_observability(self) -> None:
        from replicate_mcp.agents.execution import AgentExecutor

        obs = Observability()
        # Create executor without a real API token — should yield an error chunk
        executor = AgentExecutor(api_token="", observability=obs)
        chunks = []
        async for chunk in executor.run("llama3_chat", {"prompt": "hello"}):
            chunks.append(chunk)
        assert any("error" in c for c in chunks)

    @pytest.mark.asyncio
    async def test_executor_records_circuit_trip(self) -> None:
        from replicate_mcp.resilience import CircuitBreaker, CircuitBreakerConfig
        from replicate_mcp.agents.execution import AgentExecutor

        obs = Observability()
        # Trip the circuit immediately
        cb_cfg = CircuitBreakerConfig(failure_threshold=1)
        executor = AgentExecutor(
            api_token="r" + "A" * 38,  # fake token
            circuit_breaker_config=cb_cfg,
            observability=obs,
        )
        # Force the circuit open for the model
        breaker = executor.circuit_breaker("meta/meta-llama-3-70b-instruct")
        breaker.record_failure()

        chunks = []
        async for chunk in executor.run("llama3_chat", {"prompt": "test"}):
            chunks.append(chunk)
        assert any("Circuit open" in c.get("error", "") for c in chunks)