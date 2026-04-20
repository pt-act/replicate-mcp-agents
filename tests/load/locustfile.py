"""Locust load-test scenarios for the Replicate MCP agent system.

Sprint S8 — Hardening.

How to run:

    pip install locust
    locust -f tests/load/locustfile.py --headless \
        -u 50 -r 5 -t 60s \
        --host=http://localhost:8080

SLO targets (from docs/slos.md):
    - P95 overhead < 200ms  (A grade)
    - Error rate   < 1%     (A grade)

The scenarios here exercise:
    1. ``AgentValidator``  — pure-Python validation (zero I/O, baseline latency)
    2. ``WorkflowSimulator`` — simulated multi-step workflow with checkpointing
    3. ``RoutingBenchmark``  — cost-aware router select_model() call throughput
    4. ``DSLBenchmark``      — safe expression DSL eval throughput
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

try:
    from locust import HttpUser, TaskSet, between, events, task  # type: ignore[import-untyped]
    HAS_LOCUST = True
except ImportError:
    HAS_LOCUST = False
    # Provide no-op stubs so the module can be imported for standalone benchmark use
    class HttpUser:  # type: ignore[no-redef]
        wait_time = None
    class TaskSet:  # type: ignore[no-redef]
        pass
    def between(a, b):  # type: ignore[misc]
        return None
    def task(weight=1):  # type: ignore[misc]
        def decorator(fn): return fn
        return decorator
    class _Events:
        class _Req:
            def fire(self, **kwargs): pass
        request = _Req()
    events = _Events()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SAMPLE_PAYLOADS: list[dict[str, Any]] = [
    {"prompt": "What is machine learning?", "max_tokens": 256},
    {"prompt": "Explain neural networks", "max_tokens": 512},
    {"prompt": "Summarise this text: Hello world", "max_tokens": 128},
    {"query": "best practices for REST API design"},
    {"image_prompt": "A sunset over mountains", "width": 512, "height": 512},
]

_CANDIDATES = [
    "meta/meta-llama-3-70b-instruct",
    "black-forest-labs/flux-1.1-pro",
    "stability-ai/sdxl",
]


# ---------------------------------------------------------------------------
# Pure-Python benchmark helpers (no HTTP) — exercised via Locust events
# ---------------------------------------------------------------------------


def _bench_validator() -> None:
    """Benchmark Pydantic AgentInputModel validation."""
    from replicate_mcp.validation import AgentInputModel

    payload = random.choice(_SAMPLE_PAYLOADS)
    model = AgentInputModel(agent_id="llama3_chat", payload=payload)
    assert model.agent_id == "llama3_chat"


def _bench_router() -> None:
    """Benchmark CostAwareRouter.select_model()."""
    from replicate_mcp.routing import CostAwareRouter

    router = CostAwareRouter(strategy="thompson")
    for model in _CANDIDATES:
        router.register_model(model, initial_cost=random.uniform(0.001, 0.05))
    chosen = router.select_model(_CANDIDATES)
    assert chosen in _CANDIDATES


def _bench_dsl() -> None:
    """Benchmark SafeEvaluator.evaluate()."""
    from replicate_mcp.dsl import SafeEvaluator

    ev = SafeEvaluator()
    score = random.random()
    result = ev.evaluate("score > 0.5", {"score": score})
    assert isinstance(result, bool)


def _bench_circuit_breaker() -> None:
    """Benchmark CircuitBreaker.pre_call() / record_success() cycle."""
    from replicate_mcp.resilience import CircuitBreaker, CircuitBreakerConfig

    cfg = CircuitBreakerConfig(failure_threshold=5)
    breaker = CircuitBreaker("bench", cfg)
    for _ in range(10):
        breaker.pre_call()
        breaker.record_success()


# ---------------------------------------------------------------------------
# Locust HTTP user scenarios
# ---------------------------------------------------------------------------


class AgentValidationUser(HttpUser):
    """Simulates clients that validate agent inputs at high frequency.

    This is a pure-CPU benchmark — no Replicate API calls are made.
    Target: ≥ 500 req/s per worker with P95 < 5ms overhead.
    """

    wait_time = between(0.001, 0.005)

    @task(10)
    def validate_agent_input(self) -> None:
        """Validate a random agent payload using Pydantic."""
        start = time.perf_counter()
        try:
            _bench_validator()
            elapsed_ms = (time.perf_counter() - start) * 1000
            # Report as a custom metric via Locust events
            events.request.fire(
                request_type="PY",
                name="validator/agent_input",
                response_time=elapsed_ms,
                response_length=0,
                exception=None,
                context={},
            )
        except Exception as exc:  # noqa: BLE001
            events.request.fire(
                request_type="PY",
                name="validator/agent_input",
                response_time=0,
                response_length=0,
                exception=exc,
                context={},
            )

    @task(5)
    def validate_workflow_input(self) -> None:
        """Validate a workflow input model."""
        from replicate_mcp.validation import WorkflowInputModel

        start = time.perf_counter()
        try:
            model = WorkflowInputModel(
                workflow_name="bench-workflow",
                initial_input=random.choice(_SAMPLE_PAYLOADS),
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="PY",
                name="validator/workflow_input",
                response_time=elapsed_ms,
                response_length=0,
                exception=None,
                context={},
            )
        except Exception as exc:  # noqa: BLE001
            events.request.fire(
                request_type="PY",
                name="validator/workflow_input",
                response_time=0,
                response_length=0,
                exception=exc,
                context={},
            )

    @task(3)
    def route_model_selection(self) -> None:
        """Benchmark router select_model()."""
        start = time.perf_counter()
        try:
            _bench_router()
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="PY",
                name="router/select_model",
                response_time=elapsed_ms,
                response_length=0,
                exception=None,
                context={},
            )
        except Exception as exc:  # noqa: BLE001
            events.request.fire(
                request_type="PY",
                name="router/select_model",
                response_time=0,
                response_length=0,
                exception=exc,
                context={},
            )

    @task(2)
    def dsl_evaluation(self) -> None:
        """Benchmark safe DSL evaluator."""
        start = time.perf_counter()
        try:
            _bench_dsl()
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="PY",
                name="dsl/evaluate",
                response_time=elapsed_ms,
                response_length=0,
                exception=None,
                context={},
            )
        except Exception as exc:  # noqa: BLE001
            events.request.fire(
                request_type="PY",
                name="dsl/evaluate",
                response_time=0,
                response_length=0,
                exception=exc,
                context={},
            )

    @task(1)
    def circuit_breaker_cycle(self) -> None:
        """Benchmark circuit breaker pre_call/record_success cycle."""
        start = time.perf_counter()
        try:
            _bench_circuit_breaker()
            elapsed_ms = (time.perf_counter() - start) * 1000
            events.request.fire(
                request_type="PY",
                name="circuit_breaker/cycle",
                response_time=elapsed_ms,
                response_length=0,
                exception=None,
                context={},
            )
        except Exception as exc:  # noqa: BLE001
            events.request.fire(
                request_type="PY",
                name="circuit_breaker/cycle",
                response_time=0,
                response_length=0,
                exception=exc,
                context={},
            )


# ---------------------------------------------------------------------------
# Standalone benchmark runner (no Locust required)
# ---------------------------------------------------------------------------


def run_standalone_benchmark(
    iterations: int = 1_000,
) -> dict[str, Any]:
    """Run a standalone benchmark without the Locust harness.

    Returns a dict of ``{benchmark_name: {p50_ms, p95_ms, p99_ms, throughput_rps}}``.

    Usage::

        python -c "
        from tests.load.locustfile import run_standalone_benchmark
        import json; print(json.dumps(run_standalone_benchmark(), indent=2))
        "
    """
    import statistics

    benchmarks = {
        "validator": _bench_validator,
        "router": _bench_router,
        "dsl": _bench_dsl,
        "circuit_breaker": _bench_circuit_breaker,
    }

    results: dict[str, Any] = {}

    for name, fn in benchmarks.items():
        times_ms: list[float] = []
        start_wall = time.perf_counter()

        for _ in range(iterations):
            t0 = time.perf_counter()
            fn()
            times_ms.append((time.perf_counter() - t0) * 1000)

        elapsed = time.perf_counter() - start_wall
        sorted_times = sorted(times_ms)
        results[name] = {
            "p50_ms": round(statistics.median(sorted_times), 4),
            "p95_ms": round(sorted_times[int(0.95 * len(sorted_times))], 4),
            "p99_ms": round(sorted_times[int(0.99 * len(sorted_times))], 4),
            "throughput_rps": round(iterations / elapsed, 1),
        }

    return results


if __name__ == "__main__":
    import sys

    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000
    results = run_standalone_benchmark(iters)
    print(json.dumps(results, indent=2))