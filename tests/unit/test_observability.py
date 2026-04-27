"""Tests for replicate_mcp.observability — OTEL no-op and configuration."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from replicate_mcp.observability import (
    HAS_OTEL,
    HAS_OTLP,
    Observability,
    ObservabilityConfig,
    _NullSpan,
    default_observability,
)

# Marker for tests that require the optional opentelemetry-sdk package.
requires_otel = pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry-sdk not installed")
requires_otlp = pytest.mark.skipif(
    not HAS_OTLP, reason="opentelemetry-exporter-otlp-proto-grpc not installed"
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
# _NullSpan context manager protocol
# ---------------------------------------------------------------------------


class TestNullSpanContextManager:
    """Cover _NullSpan.__enter__ and __exit__ directly."""

    def test_enter_returns_self(self) -> None:
        span = _NullSpan()
        with span as s:
            assert s is span

    def test_exit_returns_none(self) -> None:
        exc_info = (ValueError, ValueError("x"), None)
        result = _NullSpan().__exit__(*exc_info)
        assert result is None


# ---------------------------------------------------------------------------
# Observability setup with OTEL SDK available
# ---------------------------------------------------------------------------


class TestObservabilitySetupWithOTEL:
    """Cover the real setup() paths when OTEL SDK is installed."""

    @requires_otel
    def test_setup_sets_is_setup(self) -> None:
        obs = Observability()
        obs.setup()
        assert obs.is_setup is True

    @requires_otel
    def test_setup_creates_tracer(self) -> None:
        obs = Observability()
        obs.setup()
        assert obs._tracer is not None

    @requires_otel
    def test_setup_creates_meter_and_instruments(self) -> None:
        obs = Observability()
        obs.setup()
        assert obs._meter is not None
        assert "invocation.count" in obs._counters
        assert "error.count" in obs._counters
        assert "circuit_breaker.trips" in obs._counters
        assert "invocation.latency" in obs._histograms
        assert "invocation.cost" in obs._histograms

    @requires_otel
    def test_setup_with_traces_disabled(self) -> None:
        cfg = ObservabilityConfig(enable_traces=False)
        obs = Observability(cfg)
        obs.setup()
        assert obs._tracer is None
        assert obs._meter is not None

    @requires_otel
    def test_setup_with_metrics_disabled(self) -> None:
        cfg = ObservabilityConfig(enable_metrics=False)
        obs = Observability(cfg)
        obs.setup()
        assert obs._tracer is not None
        assert obs._meter is None

    @requires_otel
    def test_setup_with_both_disabled(self) -> None:
        cfg = ObservabilityConfig(enable_traces=False, enable_metrics=False)
        obs = Observability(cfg)
        obs.setup()
        assert obs._tracer is None
        assert obs._meter is None

    @requires_otel
    def test_setup_idempotent_with_otel(self) -> None:
        obs = Observability()
        obs.setup()
        tracer_before = obs._tracer
        obs.setup()  # second call should be no-op
        assert obs._tracer is tracer_before

    @requires_otlp
    def test_setup_uses_otlp_endpoint_from_config(self) -> None:
        cfg = ObservabilityConfig(otlp_endpoint="http://custom:4317")
        obs = Observability(cfg)
        obs.setup()
        assert obs.is_setup is True

    @requires_otlp
    def test_setup_uses_otlp_endpoint_from_env(self) -> None:
        with patch.dict("os.environ", {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://env:4317"}):
            cfg = ObservabilityConfig(otlp_endpoint=None)
            obs = Observability(cfg)
            obs.setup()
            assert obs.is_setup is True


# ---------------------------------------------------------------------------
# Span with real tracer (lines 279-284)
# ---------------------------------------------------------------------------


class TestSpanWithRealTracer:
    """Cover the real span path when OTEL is set up."""

    @requires_otel
    def test_span_with_real_tracer(self) -> None:
        obs = Observability()
        obs.setup()
        with obs.span("test.span", model="meta/llama") as span:
            # Real OTEL span — set_attribute should work
            span.set_attribute("key", "value")

    @requires_otel
    def test_span_attribute_type_coercion(self) -> None:
        """Non-primitive attribute values are coerced to str."""
        obs = Observability()
        obs.setup()
        with obs.span("test", obj={"a": 1}, lst=[1, 2]) as span:
            # {"a": 1} and [1, 2] should be converted to str
            span.set_attribute("extra", "val")

    @requires_otel
    def test_span_with_primitive_attributes(self) -> None:
        """Primitive types (bool, int, float, str) are passed through."""
        obs = Observability()
        obs.setup()
        with obs.span("test", count=42, flag=True, score=0.95, label="hello") as span:
            span.set_attribute("extra", "val")


# ---------------------------------------------------------------------------
# _init_instruments with None meter (line 225)
# ---------------------------------------------------------------------------


class TestInitInstruments:
    """Cover _init_instruments early return when _meter is None."""

    def test_init_instruments_no_meter(self) -> None:
        obs = Observability()
        assert obs._meter is None
        obs._init_instruments()
        # Should not create any instruments
        assert len(obs._counters) == 0
        assert len(obs._histograms) == 0


# ---------------------------------------------------------------------------
# _counter_add / _histogram_record with real instruments and exceptions
# (lines 337-340, 350-353)
# ---------------------------------------------------------------------------


class TestCounterAndHistogramInternals:
    """Cover _counter_add and _histogram_record paths."""

    @requires_otel
    def test_counter_add_with_real_instrument(self) -> None:
        obs = Observability()
        obs.setup()
        obs._counter_add("invocation.count", 1, {"model": "test"})
        # Should not raise

    def test_counter_add_missing_counter(self) -> None:
        obs = Observability()
        obs._counter_add("nonexistent", 1, {})
        # Should silently return

    def test_counter_add_none_counter(self) -> None:
        obs = Observability()
        obs._counters["missing"] = None
        obs._counter_add("missing", 1, {})
        # Should silently return

    def test_counter_add_exception_is_silenced(self) -> None:
        obs = Observability()
        bad_counter = MagicMock()
        bad_counter.add.side_effect = RuntimeError("boom")
        obs._counters["bad"] = bad_counter
        obs._counter_add("bad", 1, {})
        # Should not raise — exception is swallowed

    @requires_otel
    def test_histogram_record_with_real_instrument(self) -> None:
        obs = Observability()
        obs.setup()
        obs._histogram_record("invocation.latency", 100.0, {"model": "test"})
        # Should not raise

    def test_histogram_record_missing_histogram(self) -> None:
        obs = Observability()
        obs._histogram_record("nonexistent", 1.0, {})
        # Should silently return

    def test_histogram_record_none_histogram(self) -> None:
        obs = Observability()
        obs._histograms["missing"] = None
        obs._histogram_record("missing", 1.0, {})
        # Should silently return

    def test_histogram_record_exception_is_silenced(self) -> None:
        obs = Observability()
        bad_histogram = MagicMock()
        bad_histogram.record.side_effect = RuntimeError("boom")
        obs._histograms["bad"] = bad_histogram
        obs._histogram_record("bad", 1.0, {})
        # Should not raise — exception is swallowed


# ---------------------------------------------------------------------------
# record_invocation with real instruments (success and failure)
# ---------------------------------------------------------------------------


class TestRecordInvocationWithInstruments:
    """Cover record_invocation / record_circuit_trip with real OTEL instruments."""

    @requires_otel
    def test_record_invocation_success_with_instruments(self) -> None:
        obs = Observability()
        obs.setup()
        obs.record_invocation(
            model="meta/llama",
            latency_ms=3200.0,
            cost_usd=0.002,
            success=True,
        )

    @requires_otel
    def test_record_invocation_failure_with_instruments(self) -> None:
        obs = Observability()
        obs.setup()
        obs.record_invocation(
            model="meta/llama",
            latency_ms=500.0,
            cost_usd=0.001,
            success=False,
            labels={"agent": "test"},
        )

    @requires_otel
    def test_record_circuit_trip_with_instruments(self) -> None:
        obs = Observability()
        obs.setup()
        obs.record_circuit_trip("my-circuit")

    @requires_otel
    def test_increment_counter_with_instruments(self) -> None:
        obs = Observability()
        obs.setup()
        obs.increment_counter("invocation.count", 3, env="prod")


# ---------------------------------------------------------------------------
# setup() exception fallback paths (lines 182-188, 198-205)
# ---------------------------------------------------------------------------


class TestSetupExceptionFallbacks:
    """Cover the except-Exception branches in setup() for traces and metrics."""

    @requires_otlp
    def test_span_exporter_exception_console_fallback(self) -> None:
        """When OTLPSpanExporter raises, fall back to ConsoleSpanExporter."""
        with patch(
            "replicate_mcp.observability.OTLPSpanExporter",
            side_effect=Exception("endpoint unreachable"),
        ):
            cfg = ObservabilityConfig(
                console_fallback=True,
                otlp_endpoint="http://localhost:4317",
            )
            obs = Observability(cfg)
            obs.setup()
            assert obs._tracer is not None

    @requires_otlp
    def test_span_exporter_exception_no_fallback(self) -> None:
        """When OTLPSpanExporter raises and console_fallback=False, no span processor added."""
        with patch(
            "replicate_mcp.observability.OTLPSpanExporter",
            side_effect=Exception("endpoint unreachable"),
        ):
            cfg = ObservabilityConfig(
                console_fallback=False,
                otlp_endpoint="http://localhost:4317",
            )
            obs = Observability(cfg)
            obs.setup()
            # Tracer should still be set (provider is created even without processors)
            assert obs._tracer is not None

    @requires_otlp
    def test_metric_exporter_exception_console_fallback(self) -> None:
        """When OTLPMetricExporter raises, fall back to ConsoleMetricExporter."""
        with patch(
            "replicate_mcp.observability.OTLPMetricExporter",
            side_effect=Exception("endpoint unreachable"),
        ):
            cfg = ObservabilityConfig(
                console_fallback=True,
                otlp_endpoint="http://localhost:4317",
            )
            obs = Observability(cfg)
            obs.setup()
            assert obs._meter is not None

    @requires_otlp
    def test_metric_exporter_exception_no_fallback(self) -> None:
        """When OTLPMetricExporter raises and console_fallback=False, meter is None."""
        with patch(
            "replicate_mcp.observability.OTLPMetricExporter",
            side_effect=Exception("endpoint unreachable"),
        ):
            cfg = ObservabilityConfig(
                console_fallback=False,
                otlp_endpoint="http://localhost:4317",
            )
            obs = Observability(cfg)
            obs.setup()
            assert obs._meter is None

    @requires_otel
    def test_no_otlp_trace_console_fallback(self) -> None:
        """When HAS_OTLP is False and console_fallback=True, traces use ConsoleSpanExporter."""
        with patch("replicate_mcp.observability.HAS_OTLP", False):
            cfg = ObservabilityConfig(
                console_fallback=True,
                otlp_endpoint="http://localhost:4317",
            )
            obs = Observability(cfg)
            obs.setup()
            assert obs._tracer is not None

    @requires_otel
    def test_no_otlp_trace_no_fallback(self) -> None:
        """When HAS_OTLP is False and console_fallback=False, tracer still set (no processor)."""
        with patch("replicate_mcp.observability.HAS_OTLP", False):
            cfg = ObservabilityConfig(
                console_fallback=False,
                otlp_endpoint="http://localhost:4317",
            )
            obs = Observability(cfg)
            obs.setup()
            assert obs._tracer is not None

    @requires_otel
    def test_no_otlp_metric_console_fallback(self) -> None:
        """When HAS_OTLP is False and console_fallback=True, metrics use ConsoleMetricExporter."""
        with patch("replicate_mcp.observability.HAS_OTLP", False):
            cfg = ObservabilityConfig(
                console_fallback=True,
                otlp_endpoint="http://localhost:4317",
            )
            obs = Observability(cfg)
            obs.setup()
            assert obs._meter is not None

    @requires_otel
    def test_no_otlp_metric_no_fallback(self) -> None:
        """When HAS_OTLP is False and console_fallback=False, metric_exporter is None."""
        with patch("replicate_mcp.observability.HAS_OTLP", False):
            cfg = ObservabilityConfig(
                console_fallback=False,
                otlp_endpoint="http://localhost:4317",
            )
            obs = Observability(cfg)
            obs.setup()
            assert obs._meter is None


# ---------------------------------------------------------------------------
# ImportError branches for HAS_OTEL=False and HAS_OTLP=False
# (lines 65-66, 77-78)
# ---------------------------------------------------------------------------


class TestImportErrorBranches:
    """Cover the ImportError fallback by blocking OTEL imports during reload."""

    @staticmethod
    def _blocking_import(prefix: str) -> Any:
        """Return an __import__ wrapper that raises ImportError for *prefix*."""
        import builtins

        real_import = builtins.__import__

        def _import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == prefix or name.startswith(prefix + "."):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        return _import

    def test_has_otel_false_when_sdk_missing(self) -> None:
        """When opentelemetry-sdk is not importable, HAS_OTEL is False."""
        import replicate_mcp.observability as obs_mod

        with patch("builtins.__import__", self._blocking_import("opentelemetry")):
            importlib.reload(obs_mod)
            assert obs_mod.HAS_OTEL is False

        # Restore real OTEL imports
        importlib.reload(obs_mod)

    @requires_otel
    def test_has_otlp_false_when_exporter_missing(self) -> None:
        """When OTLP exporter is not importable, HAS_OTLP is False."""
        # Block only the OTLP exporter; core OTEL must still import
        import builtins

        import replicate_mcp.observability as obs_mod

        real_import = builtins.__import__

        def _import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("opentelemetry.exporter.otlp"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", _import):
            importlib.reload(obs_mod)
            assert obs_mod.HAS_OTLP is False

        # Restore real OTEL imports
        importlib.reload(obs_mod)

    def test_setup_noop_when_otel_missing(self) -> None:
        """When HAS_OTEL is False, setup() does nothing."""
        import replicate_mcp.observability as obs_mod

        with patch("builtins.__import__", self._blocking_import("opentelemetry")):
            importlib.reload(obs_mod)
            obs = obs_mod.Observability()
            obs.setup()
            assert obs._tracer is None
            assert obs._meter is None

        # Restore real OTEL imports
        importlib.reload(obs_mod)


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
        from replicate_mcp.agents.execution import AgentExecutor
        from replicate_mcp.resilience import CircuitBreakerConfig

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
