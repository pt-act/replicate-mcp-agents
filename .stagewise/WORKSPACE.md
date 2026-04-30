# WORKSPACE BRIEFING: replicate-mcp-agents

## SNAPSHOT

type: single  
langs: Python  
runtimes: Python 3.10+, asyncio, anyio  
pkgManager: poetry  
deliverables: pip package, CLI binary, HTTP worker node  
rootConfigs: pyproject.toml, .github/workflows/ci.yml, .pre-commit-config.yaml  

## ARCHITECTURE

Single package. No monorepo structure. All code in `src/replicate_mcp/`.

### Core Modules

| Module | Purpose | Key Classes/Functions |
|--------|---------|----------------------|
| `sdk.py` | Fluent API + decorator | `@agent`, `AgentBuilder`, `WorkflowBuilder`, `AgentContext` |
| `server.py` | MCP server entrypoint | `serve()`, `serve_http()`, `serve_streamable_http()`, `get_asgi_app()` |
| `agents/execution.py` | Model invocation engine | `AgentExecutor` (concurrency, circuit breaker, retry, observability) |
| `agents/registry.py` | Agent metadata store | `AgentRegistry`, `AgentMetadata` |
| `routing.py` | Cost-aware + Thompson | `CostAwareRouter`, `ModelStats`, `RoutingDecision`, `RoutingWeights` |
| `qos.py` | QoS tiers + UCB1 | `QoSLevel`, `QoSPolicy`, `UCB1Router`, `AdaptiveRouter` |
| `discovery.py` | Model catalog + auto-register | `ModelDiscovery`, `DiscoveryConfig`, `discover_and_register()` |
| `plugins/` | Plugin ecosystem | `BasePlugin`, `PluginRegistry`, `load_plugins()` |
| `distributed.py` | Local + remote execution | `DistributedExecutor`, `WorkerNode`, `HttpWorkerTransport`, `RemoteWorkerNode` |
| `worker_server.py` | HTTP worker node hosting | `WorkerHttpApp`, `serve_worker()` |
| `worker_circuit_breaker.py` | Worker reliability tracking | `WorkerCircuitBreaker`, `WorkerCircuitState` (v0.8.0) |
| `resilience.py` | Circuit breaker + retry | `CircuitBreaker`, `CircuitBreakerConfig`, `RetryConfig`, `with_retry()` |
| `ratelimit.py` | Token bucket limiter | `TokenBucket` |
| `observability.py` | OpenTelemetry integration | `Observability`, `ObservabilityConfig` |
| `cache.py` | Result caching | `ResultCache`, `EvictionPolicy` |
| `validation.py` | Pydantic schemas | `AgentInputModel`, `WorkflowInputModel`, `AgentMetadataModel`, `ServerConfigModel`, `DSLExpressionModel` |
| `exceptions.py` | Error hierarchy | `ReplicateMCPError`, `RetryableError`, `NonRetryableError`, `AuthenticationError`, `RateLimitError`, `ServerError`, `ClientError` |
| `interfaces.py` | Protocol contracts | `AgentExecutorProtocol`, `AgentRegistryProtocol`, `CheckpointManagerProtocol`, `ModelRouterProtocol`, `CircuitBreakerProtocol`, `RateLimiterProtocol`, `ObservabilityProtocol` |
| `latitude.py` | Latitude.sh integration | `LatitudeClient`, `LatitudeConfig`, `LatitudePlugin`, `LatitudePrompt`, `LatitudeTrace` (v0.8.0, optional) |
| `utils/audit.py` | Invocation audit log | `AuditLogger`, `AuditRecord` |
| `utils/router_state.py` | Durable routing state | `RouterStateManager` |
| `utils/checkpointing.py` | Workflow state persistence | checkpoint utilities |
| `utils/telemetry.py` | Metrics emission | telemetry utilities |
| `utils/logging.py` | Structured logging integration | logging configuration (v0.8.0) |
| `mcp/protocol.py` | MCP protocol | MCP tool/resource definitions |
| `mcp/transport.py` | MCP transport layer | stdio, SSE, HTTP transports |
| `cli/main.py` | Click CLI | `serve`, `agents run`, `workflows run`, `workers start`, `doctor` commands |
| `security.py` | Secret management | `SecretManager` |
| `dsl.py` | DSL expression evaluator | workflow DSL evaluation |

## SUBSYSTEM DETAILS

### `src/replicate_mcp/agents/` → Agent Registration & Execution

- **Registry**: `AgentRegistry` (dict-backed, O(1) by `safe_name`). `AgentMetadata` holds model path, cost, latency, tags, input schema.
- **Registry bootstrap**: Default agents in `server.py:_DEFAULT_AGENTS`.
- **Execution**: `AgentExecutor` wraps Replicate API calls with:
  - Per-model `CircuitBreaker` (fails open after threshold)
  - `RetryConfig` exponential backoff w/ decorrelated jitter
  - `TokenBucket` rate limiting per model
  - `Observability` OTEL tracing (spans + metrics)
  - Async streaming via `AsyncIterator[dict[str, Any]]`
- **Discovery**: `ModelDiscovery` auto-discovers from Replicate API, TTL-cached, merges into registry.
- **Composition**: `WorkflowComposer` builds DAGs; tasks can run sequentially or parallel.
- **Transforms**: Agent output → next agent input connectors (`transforms.py`).

### `src/replicate_mcp/routing/` → Intelligent Model Selection

- **CostAwareRouter** (deterministic or Thompson Sampling):
  - Per-model EMA tracking: cost, latency, quality
  - Strategy: `"score"` (weighted sum) or `"thompson"` (Beta(α,β) sampling)
  - `select_model(candidates)` → best model string
  - `select_model_explain()` → `RoutingDecision` (explains why)
  - `record_outcome()` updates stats
- **UCB1Router** (deterministic multi-armed bandit):
  - Upper Confidence Bound algorithm, regret bound O(√(n log n))
  - Parameter-free, reproducible
- **AdaptiveRouter** (meta-router):
  - Auto-switches between Thompson (high variance) and UCB1 (stable)

### `src/replicate_mcp/qos/` → Service Quality Enforcement

- **QoSLevel**: `FAST` (latency <2s), `BALANCED` (all equal), `QUALITY` (favor quality).
- **QoSPolicy**: SLA bounds (latency_cap_ms, cost_cap_usd, quality_floor).
- **Pre-filter**: Remove candidates failing SLA *before* routing strategy applied.

### `src/replicate_mcp/plugins/` → Plugin Ecosystem

- **BasePlugin**: ABC `on_agent_run()`, `on_agent_result()` hooks. Return optional replacement payload.
- **PluginRegistry**: runtime registry, auto-loads from entry points.
- **Built-in guardrails**: `PIIMaskPlugin`, `ContentFilterPlugin`, `CostCapPlugin`.
- **Entry point**: `replicate_mcp.plugins` group in pyproject.toml.

### `src/replicate_mcp/distributed/` → Local + Remote Execution

- **Local**: `WorkerNode` (threaded executor on same machine), `DistributedExecutor` (routes to workers).
- **Remote**: `WorkerTransport` interface, `HttpWorkerTransport` (over HTTP), `RemoteWorkerNode` proxy.
- **Worker node**: `WorkerHttpApp` (uvicorn-based) at `/worker/run/<agent_id>`. Health check at `/health`.

### `src/replicate_mcp/worker_circuit_breaker.py` → Distributed Worker Reliability (v0.8.0)

- **WorkerCircuitBreaker**: Extends core `CircuitBreaker` for HTTP worker nodes.
- **WorkerCircuitState**: Serializable circuit state exposed via `/health` endpoint.
- **Failover strategy**: Coordinators check worker circuit state before routing; respect HALF_OPEN probe limits.
- **Circuit state transitions**: Deterministic, observable, automatic metrics integration.

### `src/replicate_mcp/server.py` → MCP Server Wiring

- **Entrypoint**: `serve()` (stdio transport, default). `serve_http()` (SSE, cloud). `serve_streamable_http()` (bidirectional HTTP).
- **Tool registration**: Every `AgentMetadata` in registry becomes MCP tool.
- **Execution flow**: MCP request → `AgentExecutor.run()` → `AsyncIterator` → MCP response chunks.

### `src/replicate_mcp/cli/main.py` → CLI Commands

- `serve`: `--transport [stdio|sse|streamable-http]`, `--host`, `--port`, `--workflows-file`.
- `agents run <agent_id>`: Execute single agent. `--model`, `--json`, `--timeout`, `--dry-run`.
- `workflows run`: Execute workflow DAG sequentially.
- `workers start`: Launch HTTP worker node. `--port`.
- `doctor`: Health checks, token validation, API connectivity, security audit checks (v0.8.0).

### `src/replicate_mcp/resilience.py` → Fault Tolerance

- **CircuitBreaker**: FSM (CLOSED→OPEN→HALF_OPEN). Tracks failures over window, opens after threshold.
- **RetryConfig**: Exponential backoff. `compute_retry_delay()` with decorrelated jitter (AWS algorithm).
- `is_retryable_error()`: Classifies exceptions (RateLimitError, ServerError → retry; AuthenticationError, ClientError → no retry).

### `src/replicate_mcp/observability.py` → OTEL Tracing

- **Observability**: Lazy OTEL setup. Spans for agent invoke, model calls. Metrics for latency, cost, success rate.
- **Null span on missing SDK**: Silent no-op if OpenTelemetry not installed.

### `src/replicate_mcp/latitude.py` → Latitude.sh Integration (v0.8.0, Optional)

- **LatitudeClient**: HTTP client for latitude.sh API (v2: project slug; v1: legacy numeric project_id).
- **Prompt Management**: Fetch, create, version prompts. Multi-turn conversations.
- **Execution**: Run prompts via Latitude API with full streaming support.
- **Tracing**: Automatic tracing of agent executions with OTEL format.
- **LatitudePlugin**: Middleware integration for automatic trace emission.
- **Optional dependency**: `pip install "replicate-mcp-agents[latitude]"` to enable.

### `src/replicate_mcp/validation.py` → Pydantic Schemas

- `AgentInputModel`: validates agent input payload
- `AgentMetadataModel`: validates agent metadata
- `WorkflowInputModel`: validates workflow input
- `ServerConfigModel`: validates server config
- `DSLExpressionModel`: validates workflow DSL expressions

## STACK

`replicate-mcp-agents`:
- framework: asyncio + anyio (async orchestration)
- api-client: replicate SDK (Replicate API)
- protocol: mcp (Model Context Protocol)
- cli: click (Click-based CLI)
- validation: pydantic (Pydantic v2)
- http: httpx, uvicorn (HTTP client + server)
- logging: structlog (structured logging)
- otel: opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc (optional)
- latitude: httpx (Latitude.sh API; optional)
- load-test: locust (optional)

## STYLE

- **typing**: Strict mypy mode. All defs fully typed. `Protocol` used for abstraction contracts.
- **async**: Heavy async/await. `anyio` for cross-platform concurrency.
- **validation**: Pydantic BaseModel for all input schemas. Raises `pydantic.ValidationError`.
- **logging**: `structlog` with JSON output. Per-module logger via `logging.getLogger(__name__)`.
- **errors**: Custom exception hierarchy. Retryable vs non-retryable classification.
- **testing**: pytest + pytest-asyncio. Mocked API calls. 40+ unit tests + integration suite.
- **naming**: snake_case. Prefixes reserved for protocols (`*Protocol`). Suffixes for config (`*Config`).
- **patterns**: Builder pattern (AgentBuilder, WorkflowBuilder). Registry pattern (AgentRegistry). Strategy pattern (routers).

## STRUCTURE

`src/replicate_mcp/` → main package  
`src/replicate_mcp/agents/` → agent registry, execution, composition  
`src/replicate_mcp/cli/` → CLI entrypoint (Click)  
`src/replicate_mcp/mcp/` → MCP protocol integration  
`src/replicate_mcp/plugins/` → plugin ecosystem  
`src/replicate_mcp/utils/` → audit, checkpointing, telemetry, router state, logging  
`tests/unit/` → 40+ unit tests (90%+ coverage required)  
`tests/integration/` → integration tests  
`tests/load/` → load tests (locustfile.py)  
`examples/` → workflow examples, notebooks  
`docs/` → ADRs, SLOs, API reference, security audit, guides  
`docs/AUDIT/` → security audit results (v0.8.0)  
`.agents/memory_bank/` → Orion-OS v2.4 memory system (local; .gitignore)  
`.github/workflows/` → CI (lint, type-check, test, security)  

## BUILD

workspaceScripts:
- `poetry install --with dev` — dev environment
- `poetry run pytest --cov-fail-under=90` — tests + coverage gate
- `poetry run mypy src/` — strict type check (37 modules)
- `poetry run ruff check .` — lint (E W F I N UP ASYNC S rules)
- `poetry run ruff format .` — format

envFiles:
- `.env` (not tracked; optional for REPLICATE_API_TOKEN, LATITUDE_API_KEY)

envPrefixes:
- `REPLICATE_API_TOKEN` — Replicate API authentication
- `REPLICATE_MCP_` — framework config (e.g., `REPLICATE_MCP_LOG_LEVEL`)
- `LATITUDE_API_KEY` — Latitude.sh API key (v0.8.0)
- `LATITUDE_PROJECT_ID` — Latitude project ID (v1 legacy; numeric)
- `LATITUDE_PROJECT_SLUG` — Latitude project slug (v2; e.g., "replicate-mcp-agents")

ci: `.github/workflows/ci.yml` → test + lint + type-check on push/PR  
ci: `.github/workflows/release.yml` → publish to PyPI on tag  
ci: `.github/workflows/security.yml` → security scanning + dependency audit  

docker: None (HTTP worker nodes run via `serve_worker()`, no Dockerfile in repo)

## KEY FILES

`replicate_mcp` (package):

| scope | file | purpose | readFor | affects |
|-------|------|---------|---------|---------|
| root | `src/replicate_mcp/__init__.py` | public API surface | new feature discovery, v0.8.0 exports | external consumers |
| root | `pyproject.toml` | build config, deps, tool configs, extras | dependency updates, latitude/otel opt-in | all builds |
| sdk | `src/replicate_mcp/sdk.py` | decorator + builders | fluent API, agent registration | all agent code |
| server | `src/replicate_mcp/server.py` | MCP server bootstrap | startup, tool registration | CLI serve command |
| exec | `src/replicate_mcp/agents/execution.py` | model invocation engine | circuit breaker, retry, observability | all invocations |
| registry | `src/replicate_mcp/agents/registry.py` | agent metadata store | agent lookup, registration | agent execution |
| routing | `src/replicate_mcp/routing.py` | cost + Thompson router | model selection strategy | agent execution |
| qos | `src/replicate_mcp/qos.py` | QoS enforcement + UCB1 | SLA pre-filter, deterministic routing | routing selection |
| discovery | `src/replicate_mcp/discovery.py` | model auto-discovery | catalog hydration, registration | registry setup |
| plugins | `src/replicate_mcp/plugins/__init__.py` | plugin ecosystem | plugin lifecycle | agent execution |
| plugins | `src/replicate_mcp/plugins/builtin.py` | guardrail plugins | PII masking, cost caps, filters | agent input/output |
| distributed | `src/replicate_mcp/distributed.py` | remote workers | HttpWorkerTransport, worker routing | distributed execution |
| worker_cb | `src/replicate_mcp/worker_circuit_breaker.py` | worker reliability (v0.8.0) | circuit state, failover logic | distributed routing |
| worker | `src/replicate_mcp/worker_server.py` | HTTP worker node | serve_worker(), worker bootstrap | remote execution |
| resilience | `src/replicate_mcp/resilience.py` | circuit breaker + retry | fault tolerance strategy | all invocations |
| observability | `src/replicate_mcp/observability.py` | OTEL tracing | span/metric emission | instrumentation |
| latitude | `src/replicate_mcp/latitude.py` | Latitude.sh integration (v0.8.0) | prompt mgmt, tracing, API calls | optional workflows |
| validation | `src/replicate_mcp/validation.py` | Pydantic schemas | input validation, serialization | all inputs |
| exceptions | `src/replicate_mcp/exceptions.py` | error hierarchy | error classification + retry logic | error handling |
| interfaces | `src/replicate_mcp/interfaces.py` | Protocol contracts | implementation decoupling | testing, mocking |
| audit | `src/replicate_mcp/utils/audit.py` | audit logging | cost tracking, invocation log | audit trails |
| logging | `src/replicate_mcp/utils/logging.py` | logging config (v0.8.0) | structured logging setup | all modules |
| dsl | `src/replicate_mcp/dsl.py` | DSL evaluator | workflow expression evaluation | workflow execution |
| cli | `src/replicate_mcp/cli/main.py` | CLI commands | command implementation, security checks | user workflows |
| sec-audit | `docs/AUDIT/SECURITY_AUDIT_REPORT.md` | security audit results | audit verification, findings | compliance |

## LOOKUP

| task | file(s) |
|------|---------|
| register agent with `@agent` decorator | `src/replicate_mcp/sdk.py` → `agent()` |
| register agent programmatically | `src/replicate_mcp/sdk.py` → `AgentBuilder` |
| launch MCP server (stdio) | `src/replicate_mcp/server.py:serve()`, `src/replicate_mcp/cli/main.py:serve` |
| launch HTTP worker node | `src/replicate_mcp/worker_server.py:serve_worker()`, `src/replicate_mcp/cli/main.py:workers start` |
| execute agent | `src/replicate_mcp/agents/execution.py:AgentExecutor.run()` |
| select model (adaptive routing) | `src/replicate_mcp/routing.py:CostAwareRouter.select_model()` |
| apply QoS filter | `src/replicate_mcp/qos.py:QoSPolicy.filter_models()` |
| discover models from API | `src/replicate_mcp/discovery.py:ModelDiscovery.refresh()` |
| add plugin | `src/replicate_mcp/plugins/loader.py` (entry point discovery) |
| implement plugin | `src/replicate_mcp/plugins/base.py:BasePlugin` (ABC) |
| circuit breaker logic | `src/replicate_mcp/resilience.py:CircuitBreaker` |
| worker circuit breaker (v0.8.0) | `src/replicate_mcp/worker_circuit_breaker.py:WorkerCircuitBreaker` |
| retry with backoff | `src/replicate_mcp/resilience.py:with_retry()` |
| classify error as retryable | `src/replicate_mcp/resilience.py:is_retryable_error()` |
| validate agent input | `src/replicate_mcp/validation.py:AgentInputModel` |
| emit OTEL span | `src/replicate_mcp/observability.py:Observability.span()` |
| run workflow DAG | `src/replicate_mcp/agents/composition.py:WorkflowComposer` |
| log invocation to audit trail | `src/replicate_mcp/utils/audit.py:AuditLogger.log()` |
| load workflows from YAML | `src/replicate_mcp/sdk.py:load_workflows_file()` |
| distribute across workers | `src/replicate_mcp/distributed.py:DistributedExecutor` |
| fetch Latitude prompt | `src/replicate_mcp/latitude.py:LatitudeClient.get_prompt()` (v0.8.0) |
| run Latitude prompt | `src/replicate_mcp/latitude.py:LatitudeClient.run_prompt()` (v0.8.0) |
| emit Latitude trace | `src/replicate_mcp/latitude.py:LatitudePlugin.on_agent_result()` (v0.8.0) |
| run health checks + security audit | `src/replicate_mcp/cli/main.py:doctor` |

## TESTING

- **test coverage**: ≥90% (enforced by CI gate)
- **unit tests**: `tests/unit/test_*.py` (40+ files; v0.8.0 adds worker_circuit_breaker + latitude tests)
- **integration tests**: `tests/integration/test_*.py`
- **load tests**: `tests/load/locustfile.py` (Locust scenarios)
- **test fixture**: `tests/fixtures/.gitkeep` (placeholder; fixtures in conftest or inline)
- **mock API**: REPLICATE_API_TOKEN="" in CI; all API calls mocked
- **pytest config**: asyncio_mode=auto, --cov=src/replicate_mcp, --strict-markers

## PHASES & FEATURES

**v0.6.0 (baseline)**:
- Phase 1: Core agent registration + MCP server
- Phase 2: Cost-aware routing (Thompson Sampling) + observability
- Phase 3: QoS tiers + plugin ecosystem
- Phase 4: Distributed execution (HTTP workers) + CLI improvements
- Phase 5a: Router state persistence + audit logging + result caching
- Phase 6: Error classification (retryable/non-retryable) + built-in guardrail plugins

**v0.8.0 (current)**:
- Phase 6 complete: Error classification verified + built-in guardails + dry-run cost estimation
- Phase 8 new: Worker circuit breaker for distributed worker reliability tracking
- Phase 7 new: Latitude.sh integration (optional extra) for prompt management + tracing + evals
- Security audit: Complete security review (docs/AUDIT/SECURITY_AUDIT_REPORT.md) — CVE patching, input validation, secret handling verified
- Memory bank: Updated to Orion-OS v2.4 structure (.agents/memory_bank/) with structured memory formats

**Roadmap**:
- Phase 9: Cost analytics dashboard + multi-tenant isolation
- Phase 10: Workflow versioning + rollback support

## ENTRY POINTS

**CLI**:
- `replicate-agent serve` — launch MCP server
- `replicate-agent agents run` — execute single agent
- `replicate-agent workflows run` — execute workflow
- `replicate-agent workers start` — launch HTTP worker node
- `replicate-agent doctor` — health checks + security audit (v0.8.0)

**Programmatic**:
- `from replicate_mcp import agent` — decorator
- `from replicate_mcp import AgentBuilder` — fluent builder
- `from replicate_mcp import DiscoveryConfig, ModelDiscovery` — auto-discovery
- `from replicate_mcp import CostAwareRouter` — cost-aware routing
- `from replicate_mcp import BasePlugin` — plugin creation
- `from replicate_mcp import WorkerCircuitBreaker` — worker circuit breaker (v0.8.0)
- `from replicate_mcp import LatitudeClient` — Latitude.sh integration (v0.8.0, if installed)

**Server**:
- `replicate_mcp.server:serve()` — stdio MCP server (default)
- `replicate_mcp.server:serve_http(host, port)` — SSE server
- `replicate_mcp.server:serve_streamable_http(host, port)` — bidirectional HTTP
- `replicate_mcp.worker_server:serve_worker(port)` — HTTP worker node

## DEPENDENCIES (High-Leverage Subset)

| package | version | category | reason |
|---------|---------|----------|--------|
| replicate | >=2.0.0b1,<3.0.0 | api-client | Replicate API SDK |
| mcp | >=1.20.0,<2.0.0 | protocol | Model Context Protocol |
| click | >=8.1.7,<9.0.0 | cli | CLI framework |
| pydantic | >=2.9.0,<3.0.0 | validation | schema validation |
| anyio | >=4.6.0,<5.0.0 | async | cross-platform concurrency |
| httpx | >=0.27.0,<0.28.0 | http | async HTTP client |
| pyyaml | >=6.0.2,<7.0.0 | config | workflow YAML parsing |
| rich | >=13.9.0,<14.0.0 | cli | terminal formatting |
| structlog | >=24.0.0,<26.0.0 | logging | structured logging |
| opentelemetry-sdk | >=1.26.0,<2.0.0 | observability | OTEL tracing (optional) |
| uvicorn | >=0.29.0,<1.0.0 | http | ASGI server (optional) |

## SECURITY & AUDIT (v0.8.0)

- **Audit Status**: ✅ Complete. See `docs/AUDIT/SECURITY_AUDIT_REPORT.md`
- **CVE Patches**: Applied in pyproject.toml:
  - starlette >=0.49.1 (CVE-2025-62727)
  - python-multipart >=0.0.26 (CVE-2026-24486, CVE-2026-40347)
  - pygments >=2.20.0 (CVE-2026-4539)
  - python-dotenv >=1.2.2 (CVE-2026-28684)
- **Key Findings**: Input validation, secret handling, error boundaries audited and verified
- **Compliance**: Security checks now integrated into `replicate-agent doctor` command

## MEMORY BANK (Orion-OS v2.4)

Path: `.agents/memory_bank/` (local; in .gitignore)

**Structure**:
- `MASTER_CONTEXT.md` — Strategic compass (AI-optimized, key-value)
- `ARCHITECTURAL_DECISIONS.md` — Decision lookup table
- `DEVELOPMENT_HISTORY.md` — Feature chronology
- `active/current_focus.md` — Next session resumption point
- `active/PROGRESS.md` — Development diary (human-readable, phase-based)
- `active/open_questions.md` — AI → Human blockers

**Implementation**: Memory formats follow `MEMORY_FORMATS.md` — structured for both AI scan speed and human narrative reading. See `.agents/MEMORY_FORMATS.md` for complete reference.

---

**Generated 2026-04-30** | `replicate-mcp-agents` v0.8.0 | 410 lines
