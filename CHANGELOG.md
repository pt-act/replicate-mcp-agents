# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] — 2026-04-20

### Added (Phase 4 · Sprints S13–S16 — Scale)

**Code Defect Fixes (from academic evaluation)**
- **`distributed.py:125`** — `asyncio.get_event_loop().create_future()` replaced with `asyncio.get_running_loop().create_future()` (PEP 3156 compliance; avoids DeprecationWarning on Python 3.10+).
- **`resilience.py:135–142`** — `CircuitBreaker.state` property is now a **pure getter** (no side effects). OPEN→HALF-OPEN recovery moved to `_maybe_recover()`, called by `can_execute()` and `pre_call()`. Property reads are now idempotent.
- **`resilience.py:__all__`** — Duplicate `RetryConfig` entry in `__all__` removed.

**CostAwareRouter Encapsulation Fix**
- **`routing.py`** — Added `sync_stats(model, *, ts_alpha, ts_beta)` public method to `CostAwareRouter`. Replaces the `# noqa: SLF001` private-attribute access in `AdaptiveRouter`.
- **`qos.py`** — `AdaptiveRouter.select_model()` updated to call `sync_stats()` instead of directly manipulating `_ts_router._stats`. Encapsulation is now clean; all `SLF001` suppressions removed.

**ModelCatalogue → ModelDiscovery Consolidation**
- **`agents/execution.py`** — `ModelCatalogue` deprecated with a `DeprecationWarning` on first use; its `discover()` delegates internally to `ModelDiscovery`. `AgentExecutor` gains a `discovery: ModelDiscovery | None` constructor parameter. `resolve_model()` now checks the `ModelDiscovery` registry first (before falling back to the legacy catalogue).
- **`discovery.py`** — Unchanged; now the canonical discovery backend.

**HTTP/SSE MCP Transport**
- **`server.py`** — Added `serve_http(host, port, mount_path, log_level)` (SSE transport), `serve_streamable_http(host, port, log_level)` (MCP 1.x Streamable HTTP), and `get_asgi_app(transport, mount_path)` (embed in existing ASGI apps). All three use the `FastMCP.sse_app()` / `FastMCP.streamable_http_app()` that are already in the MCP SDK.
- **`pyproject.toml`** — New `[http]` optional extra: `uvicorn>=0.29.0,<1.0.0`. Added to `[all]`.

**Real Network-Distributed Workers**
- **`distributed.py`** — Added `WorkerTransport` ABC (`submit`, `health_check`, `get_metrics`), `HttpWorkerTransport` (httpx-based client: `POST /execute`, `GET /health`, `GET /metrics`), `RemoteWorkerNode` (dispatches via transport, tracks load, pings, failover-aware). `DistributedExecutor` gains `add_remote_node()`, `remove_remote_node()`, `remote_nodes` property; `submit()` routes across local **and** remote nodes by least-loaded.
- **`worker_server.py`** (new) — `WorkerHttpApp` Starlette ASGI app exposing `POST /execute`, `GET /health`, `GET /metrics`. `serve_worker(host, port, api_token, node_id, log_level, max_concurrency)` launches via uvicorn. (ADR-008 extension)

**Complete CLI**
- **`cli/main.py`** — New `serve` command with `--transport [stdio|sse|streamable-http]`, `--host`, `--port`, `--mount-path`, `--log-level` options. New `workers` subgroup: `workers start` (launches HTTP worker node) and `workers ping` (health-checks a remote worker). `agents run` extended with `--model` (model-path override) and `--json` (raw chunk output) flags. `workflows run` fully implemented: resolves `WorkflowSpec` from SDK registry, executes steps sequentially via `AgentExecutor`, applies `input_map`, supports per-step `anyio.move_on_after()` timeout and `--checkpoint-dir`. `workflows list` now reads the SDK workflow registry.
- **`sdk.py`** — Added `register_workflow(spec)`, `get_workflow(name)`, `list_workflows()` backed by module-level `_workflow_registry: dict[str, WorkflowSpec]`.

### Changed
- **`__init__.py`** — Bumped to v0.5.0. Re-exports `WorkerTransport`, `HttpWorkerTransport`, `RemoteWorkerNode`, `WorkerHttpApp`, `serve_worker`, `register_workflow`, `get_workflow`, `list_workflows`.

### Tests
- 66 new tests across `test_worker_server.py` (new), `test_routing.py`, `test_distributed.py`, `test_sdk.py`, `test_server.py`, `test_execution.py`, `test_cli.py`.
- Total: **641 tests**, **90% line coverage** (gate: ≥90%).

---

## [0.4.0] — 2026-04-20

### Added (Phase 3 · Sprints S9–S12 — Differentiation)

**S9 · E11 — Dynamic Model Discovery**
- **`discovery.py`** — `ModelDiscovery` with TTL-based caching, owner/tag filters, `max_models` cap, and background refresh loop (`start_background_refresh` / `stop_background_refresh`). `DiscoveryConfig` dataclass. `DiscoveryResult` summary. `discover_and_register()` one-shot convenience function. Models auto-registered via `registry.register_or_update()` so manual customisations survive refresh.

**S9 · E12 — Fluent Python SDK**
- **`sdk.py`** — `@agent` decorator (bare and parameterised forms); `AgentBuilder` with full method-chaining (`.model()`, `.description()`, `.tag()`, `.streaming()`, `.estimated_cost()`, `.avg_latency()`, `.input_schema()`, `.build()`, `.register()`); `WorkflowBuilder` → `WorkflowSpec` (immutable) + `WorkflowStep`; `AgentContext` context manager for test isolation. (ADR-006)

**S10 · E13 — Quality-of-Service Routing**
- **`qos.py`** — `QoSLevel` enum (FAST / BALANCED / QUALITY); `QoSPolicy` with per-level default SLA constraints and `filter_candidates()` for graceful degradation; `UCB1Router` (Upper Confidence Bound 1 bandit, O(√(n log n)) regret); `AdaptiveRouter` meta-router that uses UCB1 during cold-start then switches to Thompson Sampling.

**S10 · E14 — Plugin Ecosystem**
- **`plugins/`** — `BasePlugin` ABC with lifecycle (`setup`, `teardown`) and optional hooks (`on_agent_run`, `on_agent_result`, `on_error`); `PluginMetadata` dataclass; `load_plugins()` with entry-point discovery (`replicate_mcp.plugins` group) + `extra_classes` injection for tests; `load_plugin_from_path()` for dynamic loading; `PluginRegistry` with thread-safe lifecycle management and hook dispatch (errors caught and logged). Hooks never crash the executor. (ADR-007)
- **`pyproject.toml`** — `[project.entry-points."replicate_mcp.plugins"]` group declared so the ecosystem is documented and ready.

**S11 · E15 — Distributed Execution**
- **`distributed.py`** — `WorkerNode` with configurable `asyncio.Queue`, concurrency pool, back-pressure (`NodeOverloadError`), health state (`NodeHealth`); `NodeRegistry` with `least_loaded()` routing; `DistributedExecutor` with task submission, failover on overload, `run_many()` for batch execution, `stream()` async generator; `TaskHandle` (awaitable future); `TaskResult` with timing and status. Async context manager supported. (ADR-008)

**S12 · E16 — Documentation**
- **`docs/guides/getting-started.md`** — tested 30-minute onboarding: install → CLI → discovery → QoS routing → 2-node distribution → plugins → MCP server.
- **`docs/guides/plugins.md`** — plugin development guide with lifecycle diagram, minimal example, package structure, testing patterns, and best practices.
- **`docs/api/index.md`** — complete API reference index covering all 20 public modules.
- **`docs/api/sdk.md`**, **`docs/api/discovery.md`** — API reference pages with mkdocstrings directives and usage examples.
- **`docs/adr/006.md`** — ADR for fluent SDK and `@agent` decorator design.
- **`docs/adr/007.md`** — ADR for plugin system design (entry points).
- **`docs/adr/008.md`** — ADR for distributed execution model (asyncio workers).
- **`mkdocs.yml`** — updated navigation, mkdocstrings integration (Google docstring style), Material theme with tabs and search.

### Changed

- **`__init__.py`** — bumped to `v0.4.0`; all Phase 3 public symbols re-exported from package root.
- **`pyproject.toml`** — bumped to `v0.4.0`; license changed to SPDX string; `[all]` extra inlined (Poetry 2.x compat); security pins added for starlette, python-multipart, pygments.
- **`ci.yml`** — coverage gate raised from 85% → 90% (Phase 3 exit criterion).

### Tests

- 172 new tests across `test_discovery.py`, `test_sdk.py`, `test_qos.py`, `test_plugins.py`, `test_distributed.py`.
- Total: **575 tests**, **91.47% line coverage** (gate: ≥90%).

---

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