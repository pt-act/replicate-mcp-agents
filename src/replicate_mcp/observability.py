"""OpenTelemetry observability integration.

Sprint S7 — Hardening.  Provides traces, metrics, and structured log
correlation for every Replicate agent invocation.

Architecture:
    - ``Observability`` is the single façade for all OTEL concerns.
    - It is a no-op (zero overhead) when ``opentelemetry-sdk`` is not
      installed, so the core library stays lightweight.
    - When OTEL is available, the facade wraps ``TracerProvider`` and
      ``MeterProvider`` with an OTLP exporter pointed at ``otlp_endpoint``
      (defaults to the OTEL_EXPORTER_OTLP_ENDPOINT env var or
      ``http://localhost:4317``).

Metrics emitted:
    ┌────────────────────────────────────────────────────────────────────┐
    │ replicate_mcp.invocation.count   counter  — total invocations      │
    │ replicate_mcp.invocation.latency histogram (ms)                    │
    │ replicate_mcp.invocation.cost    histogram (USD)                   │
    │ replicate_mcp.error.count        counter  — failed invocations     │
    │ replicate_mcp.circuit_breaker.trips counter — circuit open events  │
    └────────────────────────────────────────────────────────────────────┘

Span attributes (secrets are redacted via :mod:`replicate_mcp.security`):
    - ``agent.id``, ``model.id``, ``latency_ms``, ``cost_usd``,
      ``success``, ``circuit.state``

Usage::

    from replicate_mcp.observability import Observability, ObservabilityConfig

    obs = Observability(ObservabilityConfig(service_name="my-app"))
    obs.setup()  # call once at startup

    with obs.span("agent.run", agent_id="llama3") as span:
        result = await run_agent()
        obs.record_invocation("meta/llama", latency_ms=3200, cost_usd=0.002, success=True)
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Optional OTEL imports — gracefully degrade if SDK not installed
# ---------------------------------------------------------------------------

try:
    from opentelemetry import metrics as otel_metrics
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False

try:
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,  # type: ignore[import-untyped]
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,  # type: ignore[import-untyped]
    )

    HAS_OTLP = True
except ImportError:
    HAS_OTLP = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ObservabilityConfig:
    """Configuration for the :class:`Observability` façade.

    Attributes:
        service_name:     Value for the OTEL ``service.name`` resource attribute.
        otlp_endpoint:    OTLP gRPC endpoint.  If ``None``, uses the
                          ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var or
                          ``http://localhost:4317``.
        enable_traces:    Emit trace spans when ``True``.
        enable_metrics:   Emit metrics when ``True``.
        console_fallback: Emit to console when the OTLP endpoint is
                          unreachable (useful for local development).
        metric_export_interval_ms: How often to push metrics (ms).
    """

    service_name: str = "replicate-mcp-agents"
    otlp_endpoint: str | None = None
    enable_traces: bool = True
    enable_metrics: bool = True
    console_fallback: bool = True
    metric_export_interval_ms: int = 30_000


# ---------------------------------------------------------------------------
# Null span — used when OTEL is disabled
# ---------------------------------------------------------------------------


class _NullSpan:
    """No-op span for use when OpenTelemetry is not installed."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ANN401
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def set_status(self, status: Any) -> None:  # noqa: ANN401
        pass

    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Observability façade
# ---------------------------------------------------------------------------


class Observability:
    """Central façade for OpenTelemetry traces and metrics.

    A single instance should be created at application startup and
    passed to subsystems that need to emit telemetry.

    If ``opentelemetry-sdk`` is not installed, all methods silently
    no-op — the rest of the application is unaffected.

    Args:
        config: :class:`ObservabilityConfig` with service name and
                endpoint settings.  Uses defaults if not provided.
    """

    def __init__(self, config: ObservabilityConfig | None = None) -> None:
        self.config = config or ObservabilityConfig()
        self._tracer: Any = None
        self._meter: Any = None
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._is_setup = False

    def setup(self) -> None:
        """Initialise the OTEL SDK.

        Must be called once at process startup (after config is known).
        Idempotent — calling more than once is safe.
        """
        if self._is_setup or not HAS_OTEL:
            return

        resource = Resource.create({SERVICE_NAME: self.config.service_name})
        endpoint = (
            self.config.otlp_endpoint
            or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        )

        # --- Traces ---
        if self.config.enable_traces:
            tracer_provider = TracerProvider(resource=resource)
            if HAS_OTLP:
                try:
                    span_exporter = OTLPSpanExporter(endpoint=endpoint)
                    tracer_provider.add_span_processor(
                        BatchSpanProcessor(span_exporter)
                    )
                except Exception:  # noqa: BLE001
                    if self.config.console_fallback:
                        tracer_provider.add_span_processor(
                            BatchSpanProcessor(ConsoleSpanExporter())
                        )
            elif self.config.console_fallback:
                tracer_provider.add_span_processor(
                    BatchSpanProcessor(ConsoleSpanExporter())
                )
            otel_trace.set_tracer_provider(tracer_provider)
            self._tracer = otel_trace.get_tracer(self.config.service_name)

        # --- Metrics ---
        if self.config.enable_metrics:
            if HAS_OTLP:
                try:
                    metric_exporter = OTLPMetricExporter(endpoint=endpoint)
                except Exception:  # noqa: BLE001
                    metric_exporter = ConsoleMetricExporter() if self.config.console_fallback else None  # type: ignore[assignment]
            elif self.config.console_fallback:
                metric_exporter = ConsoleMetricExporter()  # type: ignore[assignment]
            else:
                metric_exporter = None  # type: ignore[assignment]

            if metric_exporter is not None:
                reader = PeriodicExportingMetricReader(
                    metric_exporter,
                    export_interval_millis=self.config.metric_export_interval_ms,
                )
                meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=[reader],
                )
                otel_metrics.set_meter_provider(meter_provider)
                self._meter = otel_metrics.get_meter(self.config.service_name)
                self._init_instruments()

        self._is_setup = True

    def _init_instruments(self) -> None:
        """Pre-create all metric instruments."""
        if self._meter is None:
            return

        self._counters["invocation.count"] = self._meter.create_counter(
            name="replicate_mcp.invocation.count",
            description="Total number of agent invocations",
        )
        self._counters["error.count"] = self._meter.create_counter(
            name="replicate_mcp.error.count",
            description="Number of failed agent invocations",
        )
        self._counters["circuit_breaker.trips"] = self._meter.create_counter(
            name="replicate_mcp.circuit_breaker.trips",
            description="Number of times a circuit breaker opened",
        )
        self._histograms["invocation.latency"] = self._meter.create_histogram(
            name="replicate_mcp.invocation.latency",
            description="Agent invocation latency in milliseconds",
            unit="ms",
        )
        self._histograms["invocation.cost"] = self._meter.create_histogram(
            name="replicate_mcp.invocation.cost",
            description="Agent invocation cost in USD",
            unit="USD",
        )

    # ---- public API ----

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        **attributes: Any,
    ) -> Generator[Any, None, None]:
        """Create a trace span as a context manager.

        If OTEL is not available, yields a no-op :class:`_NullSpan`.

        Args:
            name:        Span name (e.g. ``"agent.run"``).
            **attributes: Initial span attributes.

        Yields:
            The active span (OTEL span or :class:`_NullSpan`).

        Example::

            with obs.span("replicate.call", model="meta/llama") as span:
                result = await call_replicate()
                span.set_attribute("result_length", len(result))
        """
        if self._tracer is None:
            yield _NullSpan()
            return

        with self._tracer.start_as_current_span(name) as active_span:
            for k, v in attributes.items():
                active_span.set_attribute(k, str(v) if not isinstance(v, bool | int | float | str) else v)
            yield active_span

    def record_invocation(
        self,
        model: str,
        latency_ms: float,
        cost_usd: float,
        success: bool,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Record a single agent invocation in the metrics backend.

        Args:
            model:      Replicate model identifier.
            latency_ms: Wall-clock latency in milliseconds.
            cost_usd:   USD cost charged for this call.
            success:    Whether the call succeeded.
            labels:     Extra OTEL attribute labels (e.g. ``{"agent": "..."}``)
        """
        attrs = {"model": model, **(labels or {})}
        self._counter_add("invocation.count", 1, attrs)
        if not success:
            self._counter_add("error.count", 1, attrs)
        self._histogram_record("invocation.latency", latency_ms, attrs)
        self._histogram_record("invocation.cost", cost_usd, attrs)

    def record_circuit_trip(self, circuit_name: str) -> None:
        """Increment the circuit-breaker trip counter."""
        self._counter_add("circuit_breaker.trips", 1, {"circuit": circuit_name})

    def increment_counter(
        self,
        name: str,
        value: int = 1,
        **labels: str,
    ) -> None:
        """Increment an arbitrary named counter.

        Creates the counter on first use if it does not exist.
        """
        self._counter_add(name, value, labels)

    # ---- internals ----

    def _counter_add(
        self,
        name: str,
        value: int | float,
        attrs: dict[str, Any],
    ) -> None:
        if name not in self._counters or self._counters[name] is None:
            return
        try:
            self._counters[name].add(value, attrs)
        except Exception:  # noqa: BLE001, S110
            pass  # never let telemetry crash the main path

    def _histogram_record(
        self,
        name: str,
        value: float,
        attrs: dict[str, Any],
    ) -> None:
        if name not in self._histograms or self._histograms[name] is None:
            return
        try:
            self._histograms[name].record(value, attrs)
        except Exception:  # noqa: BLE001, S110
            pass  # never let telemetry crash the main path

    @property
    def is_setup(self) -> bool:
        """Return ``True`` if :meth:`setup` has been called."""
        return self._is_setup

    @property
    def otel_available(self) -> bool:
        """Return ``True`` if the ``opentelemetry-sdk`` package is installed."""
        return HAS_OTEL


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

#: Shared default :class:`Observability` instance.
#: Call ``default_observability.setup()`` once at startup.
default_observability = Observability()


__all__ = [
    "ObservabilityConfig",
    "Observability",
    "default_observability",
    "HAS_OTEL",
    "HAS_OTLP",
]
