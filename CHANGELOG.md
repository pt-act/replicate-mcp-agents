# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-04-20

### Added (Phase 2 · Sprints S5–S8 — Hardening)

**S5 · Protocol Abstractions + Validation + Security**
- **`interfaces.py`** — 8 `@runtime_checkable` `typing.Protocol` ABCs covering every subsystem (`AgentExecutorProtocol`, `AgentRegistryProtocol`, `CheckpointManagerProtocol`, `TelemetryTrackerProtocol`, `ModelRouterProtocol`, `CircuitBreakerProtocol`, `RateLimiterProtocol`, `ObservabilityProtocol`). All concrete classes verified to conform in tests. (ADR-003)
- **`validation.py`** — Pydantic v2 models for all external inputs: `AgentInputModel`, `WorkflowInputModel`, `AgentMetadataModel`, `ServerConfigModel`, `DSLExpressionModel`. Enforced in CLI and server. Full validators for safe_name format, model path format, payload size, resume-without-checkpoint guard.
- **`security.py`** — `SecretManager` (env + keyring resolution, never logs secrets), `SecretMasker` (key-based + pattern-based redaction), `sanitize_otel_attributes()`, `assert_no_eval_in_config()`, `InsecureConfigError`. `REPLICATE_API_TOKEN` validation added.
- **CI: `.github/workflows/security.yml`** — security-specific workflow with pip-audit, Bandit SAST (SARIF upload), Semgrep, detect-secrets scan, CycloneDX SBOM generation.
- **`.pre-commit-config.yaml`** — updated with detect-secrets, Bandit, standard hooks (trailing whitespace, end-of-file, YAML/JSON/TOML checks, no-commit-to-branch).

**S6 · Resilience — Circuit Breaker + Safe DSL**
- **`resilience.py`** — `CircuitBreaker` (3-state FSM: CLOSED/OPEN/HALF_OPEN, configurable thresholds), `RetryConfig` (exponential backoff with decorrelated jitter), `compute_retry_delay()`, `with_retry()` async helper, `retry_iter()` for streaming generators. `CircuitOpenError`, `MaxRetriesExceededError`. (ADR-004)
- **`dsl.py`** — `SafeEvaluator` with explicit AST whitelist, dunder-access blocking, f-string blocking, safe builtins, `CompiledTransform` for repeated evaluation, `safe_eval()` convenience function. Replaces any remaining eval()-based transform patterns. `UnsafeExpressionError`, `ExpressionSyntaxError`.

**S7 · Cost-Aware Routing + Rate Limiting + Observability**
- **`routing.py`** — `CostAwareRouter` with two strategies: Thompson Sampling (default) over Beta(α, β) posterior and weighted score with EMA predictions. `ModelStats` (EMA latency, cost, quality; Thompson Sampling state; `success_rate`). `RoutingWeights`. `leaderboard()` sorted by EMA cost. Router wired into `server.py` with `routing://leaderboard` MCP resource. (ADR-005)
- **`ratelimit.py`** — `TokenBucket` (async, lock-safe, configurable rate + capacity), `RateLimiter` (named-bucket registry with fluent `.add()` chaining). Wired into `AgentExecutor` as optional `rate_limiter` argument.
- **`observability.py`** — `Observability` façade over OpenTelemetry SDK with zero-overhead no-op when OTEL not installed. Emits 5 instruments: `invocation.count`, `invocation.latency`, `invocation.cost`, `error.count`, `circuit_breaker.trips`. `span()` context manager. `default_observability` module singleton. `ObservabilityConfig` (service name, OTLP endpoint, console fallback). OTEL is an optional install extra (`pip install "replicate-mcp-agents[otel]"`).

**S8 · Ops + Load Testing**
- **`tests/load/locustfile.py`** — Locust load-test scenarios for 4 benchmarks (validator, router, DSL, circuit breaker). `run_standalone_benchmark()` for no-harness profiling.
- **`docs/slos.md`** — 6 SLO definitions (availability, P95 overhead, error rate, cost tracking accuracy, MTTR, circuit recovery) with A/A+ targets, error budget policy, monitoring stack, and Grafana dashboard reference.
- **`docs/runbooks/top-10-failures.md`** — Operations runbook covering: REPLICATE_API_TOKEN not set, circuit stuck OPEN, rate limiting, checkpoint corruption, MCP connection failure, unsafe DSL expression, high memory usage, OTEL collector unreachable, DAG cycle at runtime, cost tracking divergence. Each entry includes immediate actions, root-cause steps, and remediation.
- **`docs/adr/003-protocol-interfaces.md`** — ADR for Protocol ABCs decision.
- **`docs/adr/004-resilience-patterns.md`** — ADR for circuit breaker + retry decision.
- **`docs/adr/005-cost-routing.md`** — ADR for Thompson Sampling routing decision.

### Changed

- **`agents/execution.py`** — `AgentExecutor` now accepts `circuit_breaker_config`, `rate_limiter`, and `observability` arguments. Retry logic uses `resilience.RetryConfig` / `compute_retry_delay` instead of hand-rolled jitter. Per-model `CircuitBreaker` dict. OTEL `span()` wraps every `run()` call. Removed internal `_decorrelated_jitter` function (superseded by `resilience.compute_retry_delay`).
- **`server.py`** — `CostAwareRouter` instantiated at module level; every default agent registered with router. `AgentExecutor` constructed with `default_observability`. `models://list` resource now includes routing stats. New `routing://leaderboard` resource. `default_observability.setup()` called at import time.
- **`cli/main.py`** — Full rewrite with Rich streaming output (progress bar, spinner, panel), coloured error messages, Pydantic input validation on all commands, `SecretManager` token check, new `agents list` / `agents run` / `status` sub-commands.
- **`pyproject.toml`** — version bumped to 0.3.0. Optional extras: `[otel]`, `[load]`, `[all]`.
- **`.github/workflows/ci.yml`** — coverage gate raised from 75 % → 85 %.
- **`.pre-commit-config.yaml`** — added detect-secrets, Bandit, and standard hooks.

### Tests

- **403 tests** (up from 164). **90% coverage** (up from 83%). New test files: `test_interfaces.py` (17 tests), `test_validation.py` (36 tests), `test_security.py` (27 tests), `test_resilience.py` (27 tests), `test_dsl.py` (35 tests), `test_routing.py` (23 tests), `test_ratelimit.py` (22 tests), `test_observability.py` (17 tests), `test_cli.py` (26 tests), `test_server.py` (9 tests).

## [0.2.0] — 2026-04-20

### Added
- **DAG Engine** — full topological sort (Kahn's algorithm) and DFS 3-colour cycle detection. `add_edge()` now rejects cycles at insertion time with `CycleDetectedError` and detailed cycle path. (`agents/composition.py`)
- **Parallel Fan-Out** — workflow `execute()` runs nodes at the same DAG level concurrently via `anyio.create_task_group()`. Verified with diamond and fan-out topologies.
- **Checkpoint-Integrated Execution** — per-level atomic checkpoint save during workflow execution, with `resume_from` support to skip completed levels.
- **Edge Transforms & Conditions** — `execute()` applies `WorkflowEdge.transform` and `WorkflowEdge.condition` between nodes, wired to the `TransformRegistry`.
- **Registry v2** — dict-backed `AgentRegistry` with O(1) `get()`/`has()` lookup, `DuplicateAgentError` on `register()`, `remove()`, `list_agents()`, `filter_by_tag()`, and `clear()`.
- **AgentExecutor v2** — concurrency limiter (semaphore), decorrelated jitter retry, `ModelCatalogue` for dynamic model discovery, and `resolve_model()` that checks catalogue after static map.
- **Model Catalogue** — `ModelCatalogue` with `discover()` (Replicate API hydration), TTL-based staleness check, `add()`/`get()` for caching model metadata.
- **Exception Hierarchy** — 13 domain-specific exceptions all inheriting from `ReplicateMCPError`: `CycleDetectedError`, `NodeNotFoundError`, `WorkflowValidationError`, `ModelNotFoundError`, `ExecutionError`, `ExecutionTimeoutError`, `TokenNotSetError`, `DuplicateAgentError`, `AgentNotFoundError`, `TransformNotFoundError`, `ConditionNotFoundError`, `CheckpointCorruptedError`.
- **Structured Logging** — `configure_logging()` and `get_logger()` using `structlog` (JSON in prod, coloured console in dev), with stdlib fallback if structlog is unavailable.
- **Atomic Checkpoints** — `CheckpointManager.save()` uses `tempfile` + `os.replace()` for crash-safe writes, monotonic version tracking, `list_sessions()`, `delete()`, `exists()`.
- **MCP Server v2** — registers multiple default agents (llama3_chat, flux_pro), exposes `models://list` resource, uses new registry `list_agents()` API.
- **164 tests** — up from 72. Coverage 83% (up from 75%). New tests for DAG cycle detection, topological sort, workflow execution, fan-out, transforms, conditions, exceptions, logging, registry v2, checkpointing v2, mocked Replicate integration.
- **ADR-002** implicit — structlog integration decision documented in this changelog.

### Changed
- **AgentMetadata** — added `model` field (full Replicate model path), `tags` list, and `replicate_model()` method.
- **CheckpointManager** — envelope format with `_meta` (version, session_id, saved_at) wrapping `state`; backward-compatible loading of old flat format.
- **AgentWorkflow** — fully rewritten from placeholder to production DAG engine with adjacency list, validation, and level-based concurrent execution.
- **`structlog`** added to core dependencies.
- Version bumped to 0.2.0.

## [0.1.0] — 2026-04-20

### Added
- **MCP Server** — functional server using the official `mcp` SDK (`FastMCP`), registers Replicate models as MCP tools, stdio transport. (`server.py`)
- **Replicate Integration** — `AgentExecutor` invokes Replicate models via the v2 SDK with streaming support and structured error handling. (`agents/execution.py`)
- **Safe Transform Registry** — `TransformRegistry` for named transforms and conditions, replacing string-encoded lambdas. Eliminates `eval()` security vulnerability. (`agents/transforms.py`)
- **72 unit tests** covering telemetry, checkpointing, MCP protocol, agent registry, composition, transforms, execution, and CLI smoke tests.
- **ADR-001** — documents dependency cleanup decisions (Pydantic, Typer, MCP SDK).
- **CONTRIBUTING.md** — development workflow, sprint cadence, coding standards.

### Fixed
- **TelemetryEvent timestamp bug** — replaced mutable default `datetime.utcnow()` with `field(default_factory=...)` using timezone-aware `datetime.now(timezone.utc)`.
- **eval() security vulnerability (CWE-94)** — removed string-encoded lambda expressions from YAML workflow examples. All transforms now use the safe `TransformRegistry`.
- **Deprecated `datetime.utcnow()`** — migrated to `datetime.now(timezone.utc)` for Python 3.12+ compatibility.

### Changed
- **MCP SDK** promoted from dev-group git dependency to core runtime dependency (`mcp >=1.20.0,<2.0.0`).
- **Typer** optional dependency removed — Click is the sole CLI framework.
- **Python requirement** relaxed from `~=3.11` to `>=3.10`.
- **YAML workflow examples** updated to use named transform/condition references instead of string lambdas.
- **README** rewritten to accurately reflect current project state with status table.

### Removed
- `cli-extras` optional dependency group (contained Typer).
- String-encoded lambda expressions from all YAML examples.