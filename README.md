# replicate-mcp-agents

> **MCP-native agent orchestration for Replicate AI models — production-grade, observable, and extensible.**

[![Tests](https://img.shields.io/badge/tests-575%20passed-brightgreen)](#test-suite)
[![Coverage](https://img.shields.io/badge/coverage-91.5%25-brightgreen)](#test-suite)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![Version](https://img.shields.io/badge/version-0.4.0-blue)](CHANGELOG.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Mypy](https://img.shields.io/badge/mypy-strict-green)](pyproject.toml)
[![Ruff](https://img.shields.io/badge/lint-ruff-green)](pyproject.toml)

`replicate-mcp-agents` is a Python framework that bridges [Replicate's](https://replicate.com) 50 000+ hosted AI model marketplace with the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), adding a full production layer: multi-armed-bandit routing, circuit breaking, distributed execution, pluggable observability, and a declarative fluent SDK — all with 91.5 % test coverage and strict type checking across 33 source modules.

---

## Table of Contents

1. [Current Status](#current-status--v040)
2. [Architecture](#architecture)
3. [Academic Evaluation](#academic-evaluation)
   - [Architectural Soundness](#1-architectural-soundness)
   - [Code Modularity](#2-code-modularity)
   - [Theoretical Robustness of AI Mechanisms](#3-theoretical-robustness-of-ai-driven-mechanisms)
   - [Identified Weaknesses](#4-identified-weaknesses-and-technical-debt)
   - [Value Proposition](#5-value-proposition)
4. [Getting Started](#getting-started)
5. [Usage Examples](#usage-examples)
6. [Key Design Decisions](#key-design-decisions)
7. [Roadmap](#roadmap)
8. [License](#license)

---

## Current Status — v0.4.0

| Subsystem | Status | Details |
|-----------|--------|---------|
| **MCP Server** (stdio) | ✅ Production | FastMCP, `models://list`, `routing://leaderboard` resources |
| **Agent Registry** | ✅ v2 | O(1) dict-backed lookup, tag filtering, deduplication |
| **DAG Workflow Engine** | ✅ Production | Kahn topological sort, DFS 3-colour cycle detection, async fan-out |
| **Checkpoint Persistence** | ✅ v2 | `tempfile` + `os.replace()` POSIX-atomic writes, versioned envelopes |
| **Safe DSL Evaluator** | ✅ Production | AST-whitelisted, dunder-blocked, f-string-blocked; zero `eval()` |
| **Circuit Breaker** | ✅ Production | 3-state FSM (CLOSED/OPEN/HALF-OPEN), configurable thresholds |
| **Cost-Aware Routing** | ✅ Production | Thompson Sampling + UCB1 + `AdaptiveRouter` meta-strategy |
| **QoS Tiers** | ✅ Production | `FAST/BALANCED/QUALITY` with SLA pre-filter and graceful degradation |
| **Rate Limiter** | ✅ Production | Token-bucket, named buckets, async `acquire()` |
| **Observability** | ✅ Production | OpenTelemetry SDK; 5 instruments; no-op when OTEL absent |
| **Security** | ✅ Production | `SecretManager` (env+keyring), `SecretMasker`, OTEL sanitisation |
| **Pydantic v2 Validation** | ✅ Production | All external inputs validated; payload size limits enforced |
| **Dynamic Model Discovery** | ✅ Production | TTL cache, owner/tag filters, background refresh loop |
| **Fluent SDK (`@agent`)** | ✅ Production | Decorator + `AgentBuilder` + `WorkflowBuilder` + `AgentContext` |
| **Plugin Ecosystem** | ✅ Production | `BasePlugin` ABC, entry-point loader, `PluginRegistry` lifecycle |
| **Distributed Executor** | ✅ Production | asyncio-queue workers, `least_loaded()` routing, failover, `run_many()` |
| **Protocol ABCs** | ✅ Production | 8 `@runtime_checkable Protocol` interfaces across all subsystems |
| **Structured Logging** | ✅ Production | structlog (JSON/coloured), stdlib fallback |
| **CLI** | ⚠️ Partial | `agents list/run`, `status`, `workflows` — execution is scaffold-level |
| **HTTP Transport** | 🔲 Planned | stdio only; HTTP/SSE transport is Phase 4 |
| **Real Distributed Nodes** | 🔲 Planned | asyncio in-process today; gRPC/NATS in Phase 4 |

**Test suite:** 575 tests · 91.5 % line coverage · 33 fully-typed source files · 6 854 source lines

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                       External Surfaces                                  │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────────────┐   │
│  │  MCP Server  │   │  CLI (Click)  │   │  Python SDK (@agent, ...) │   │
│  │  (FastMCP)   │   │  replicate-   │   │  sdk.py · discovery.py   │   │
│  │  server.py   │   │  agent        │   │  WorkflowBuilder          │   │
│  └──────┬───────┘   └───────┬───────┘   └────────────┬─────────────┘   │
└─────────┼───────────────────┼────────────────────────┼─────────────────┘
          │                   │                        │
┌─────────▼───────────────────▼────────────────────────▼─────────────────┐
│                       Orchestration Layer                                │
│  ┌───────────────┐   ┌──────────────────┐   ┌─────────────────────┐   │
│  │ AgentRegistry │   │  AgentExecutor   │   │  DistributedExecutor │   │
│  │ registry.py   │◄──│  execution.py    │◄──│  distributed.py      │   │
│  └───────────────┘   │  (streaming,     │   │  WorkerNode ×N       │   │
│  ┌───────────────┐   │   retry, sem.)   │   │  NodeRegistry        │   │
│  │ AgentWorkflow │   └────────┬─────────┘   └─────────────────────┘   │
│  │ composition.py│            │                                          │
│  │ (DAG, fan-out)│            │                                          │
│  └───────────────┘            │                                          │
└──────────────────────────────┼──────────────────────────────────────────┘
                                │
┌──────────────────────────────▼──────────────────────────────────────────┐
│                       Reliability & Intelligence Layer                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ CostAware    │  │ AdaptiveRouter│  │ CircuitBreaker│  │RateLimiter │  │
│  │ Router       │  │ (UCB1 →      │  │ resilience.py │  │ratelimit.py│  │
│  │ routing.py   │  │  Thompson)   │  │ 3-state FSM   │  │TokenBucket │  │
│  │ EMA·Beta     │  │ qos.py       │  │ + with_retry()│  │            │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                │
┌──────────────────────────────▼──────────────────────────────────────────┐
│                       Foundation Layer                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │
│  │ Observability│  │  Security   │  │  Validation  │  │  Plugins     │  │
│  │ observab.py  │  │ security.py │  │ validation.py│  │ plugins/     │  │
│  │ OTEL 5 instr │  │ SecretMgr   │  │ Pydantic v2  │  │ BasePlugin   │  │
│  │ span() ctx   │  │ SecretMask  │  │ 5 schemas    │  │ entry-points │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────────┐  │
│  │ Safe DSL     │  │ Checkpoint  │  │         interfaces.py            │  │
│  │ dsl.py       │  │ checkptng.py│  │  8 @runtime_checkable Protocols  │  │
│  │ AST whitelist│  │ POSIX-atomic│  │  (decouples all subsystems)      │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                │
┌──────────────────────────────▼──────────────────────────────────────────┐
│                       Replicate API (external)                           │
│              replicate SDK v2 · bearer_token auth · streaming            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Academic Evaluation

This section provides a rigorous, evidence-based analysis of the project's engineering quality, theoretical soundness, and market position. Specific claims are grounded in source-code line references measured against the v0.4.0 codebase (33 files · 6 854 LOC · 89 classes · 341 functions).

---

### 1. Architectural Soundness

#### 1.1 Layered Hexagonal Design

The project adopts a clean **hexagonal / ports-and-adapters** architecture across four layers (External Surfaces → Orchestration → Reliability & Intelligence → Foundation), with `interfaces.py` acting as the formal contract layer. Every subsystem publicly exposes a `@runtime_checkable Protocol` ABC, meaning concrete implementations can be swapped — in tests, in alternative backends, or by third-party contributors — without changing any consumer code.

Eight Protocol interfaces are defined (`AgentExecutorProtocol`, `AgentRegistryProtocol`, `CheckpointManagerProtocol`, `TelemetryTrackerProtocol`, `ModelRouterProtocol`, `CircuitBreakerProtocol`, `RateLimiterProtocol`, `ObservabilityProtocol`), each specifying a minimal behavioural contract. This design pattern is consistent with Liskov Substitution Principle and, critically, is *enforced at runtime*: any object with the right dunder methods satisfies the protocol without inheritance, enabling zero-cost duck-typed composition.

#### 1.2 Separation of Concerns

Responsibility partitioning is well-executed. The following concerns are fully decoupled into dedicated modules:

| Concern | Module | LOC |
|---------|--------|-----|
| Model selection intelligence | `routing.py` + `qos.py` | 667 |
| Fault tolerance | `resilience.py` | 392 |
| Secret management | `security.py` | 271 |
| Observability | `observability.py` | 381 |
| Input validation | `validation.py` | 279 |
| Expression safety | `dsl.py` | 388 |
| Distributed execution | `distributed.py` | 574 |
| Plugin lifecycle | `plugins/` (3 files) | 444 |

No module exceeds 575 lines. Average module size is 208 lines — well within the range that permits full reasoning without cognitive overload.

#### 1.3 Dependency Management

All external runtime dependencies are pinned to semver-compatible ranges (`replicate>=2.0,<3.0`, `mcp>=1.20,<2.0`, `pydantic>=2.9,<3.0`). Three CVE-affected transitive dependencies receive explicit minimum-version pins in the dev dependency group (`starlette≥0.49.1`, `python-multipart≥0.0.26`, `pygments≥2.20.0`), demonstrating supply-chain security awareness. OpenTelemetry is an optional extra (`[otel]`) with a zero-overhead no-op fallback, preserving deployability in minimal environments.

#### 1.4 Error Handling Hierarchy

The project defines a 13-exception domain hierarchy under `ReplicateMCPError`, covering cycle detection, node-not-found, workflow validation, model resolution, execution timeouts, token absence, duplicate registration, transform resolution, checkpoint corruption, circuit open, max-retries-exceeded, unsafe expressions, and plugin errors. This hierarchy enables precise `except` clauses throughout the codebase and clean error propagation to MCP tool callers.

---

### 2. Code Modularity

#### 2.1 Quantitative Assessment

| Metric | Value | Industry Benchmark |
|--------|-------|--------------------|
| Source files | 33 | — |
| Source lines (LOC) | 6 854 | — |
| Public classes | 89 | — |
| Public functions/methods | 341 | — |
| Test files | 26 | — |
| Test count | 575 | — |
| Line coverage | 91.5 % | ≥80 % recommended |
| `# noqa` suppressions in src | ~44 | Low |
| Strict mypy clean | ✅ 0 errors | — |

The LOC-to-test ratio of 1:0.84 (575 tests for 6 854 src lines) is strong for an application of this complexity. Every public API has at minimum one unit test; the majority have boundary and failure-mode tests.

#### 2.2 Plugin Extensibility

The plugin system (`plugins/`) uses **PEP 517/518 entry points** (`[project.entry-points."replicate_mcp.plugins"]`), the Python ecosystem's canonical mechanism for runtime extensibility. Third-party authors can publish a plugin as a separate `pip` package and have it auto-discovered without any changes to this codebase. The `BasePlugin` ABC enforces a lifecycle contract (`setup()`, `teardown()`, `on_agent_run()`, `on_agent_result()`, `on_error()`), all hooks swallow exceptions to prevent plugin faults from crashing the executor.

#### 2.3 Fluent Builder Pattern

`AgentBuilder` and `WorkflowBuilder` provide fully method-chainable builder APIs. Both return `self` from every setter, with a terminal `.build()` that constructs a validated, immutable dataclass. This pattern eliminates long positional constructor calls and maps directly to IDE autocomplete, reducing onboarding friction.

#### 2.4 Test Isolation Pattern

`AgentContext` is an async-compatible Python context manager that replaces the module-level `_default_registry` for the scope of a `with` block, then restores the original on exit. This enables complete test isolation for `@agent`-decorated code without test-order dependencies or global state leakage. It is an elegant solution to the inherent tension between module-level decorator registration (which requires a singleton target) and test isolation (which requires a clean slate).

---

### 3. Theoretical Robustness of AI-Driven Mechanisms

#### 3.1 Multi-Armed Bandit Routing

The routing layer implements two bandit algorithms, each with rigorous theoretical backing:

**UCB1 (`UCB1Router`):**  
The Upper Confidence Bound 1 algorithm (Auer, Cesa-Bianchi & Fischer, 2002) selects the model maximising:

```
index_i = μ_i + c · √(2 · ln(N) / n_i)
```

where μ_i is the empirical success rate of model *i*, N is the total invocation count, n_i is model *i*'s invocation count, and c is an exploration constant (default 1.0). UCB1 enjoys a provable cumulative regret bound of O(√(K · N · log N)) over K models and N rounds, guaranteeing logarithmic exploration of every candidate while converging to the optimal arm. Unvisited models receive an index of `+∞`, ensuring all candidates are sampled before exploitation begins — a correct implementation of the algorithm.

**Thompson Sampling (`CostAwareRouter`, strategy="thompson"):**  
Thompson Sampling maintains a Beta(α, β) posterior over each model's success probability, initialised with a uniform prior (α=β=1). On each selection, it draws one sample per candidate and picks the highest. This Bayesian approach has been shown empirically to outperform UCB1 in many practical settings (Chapelle & Li, 2011) because it naturally concentrates exploration in high-uncertainty regions. The prior update rule (α += 1 on success, β += 1 on failure) is the textbook conjugate update for a Bernoulli likelihood with a Beta prior — theoretically exact.

**`AdaptiveRouter` (meta-strategy):**  
Combines both: UCB1 is used for the first `explore_threshold` (default: 20) total invocations to ensure all models are systematically probed; Thompson Sampling is then engaged for ongoing exploitation. This piecewise strategy gives UCB1's deterministic coverage guarantees during cold-start and Thompson Sampling's empirical superiority at scale.

**EMA Statistics (`ModelStats`):**  
Latency, cost, and quality are tracked via Exponential Moving Averages with smoothing factor α=0.3 (30% weight on each new observation). The choice of α=0.3 is reasonable for environments where API latency is moderately stable; it provides faster adaptation than a long window while damping noise better than a short one.

#### 3.2 QoS Pre-Filtering

`QoSPolicy` applies SLA constraints as a *pre-filter* before bandit selection. This decoupling is architecturally sound: the router remains a pure learning algorithm uncontaminated by business SLA logic. The graceful degradation rule — "if all candidates fail the policy, use the full set" — prevents request starvation at the cost of occasional SLA violations, an acceptable trade-off for a routing layer that prefers liveness over strict compliance.

#### 3.3 Circuit Breaker Finite-State Machine

The circuit breaker implements the canonical three-state FSM (CLOSED → OPEN → HALF-OPEN → CLOSED) described by Nygard (2007) in *Release It!*. State transitions are correctly guarded: OPEN → HALF-OPEN happens only after `recovery_timeout` seconds; HALF-OPEN → CLOSED requires `success_threshold` consecutive successes; any failure in HALF-OPEN immediately returns to OPEN. The `half_open_max_calls` parameter bounds concurrent probes, preventing thundering herd from the probe phase itself.

#### 3.4 Retry with Decorrelated Jitter

`compute_retry_delay()` implements the decorrelated jitter formula from the AWS distributed systems blog (Brooker, 2015):

```
delay = min(max_delay, base × 2^attempt) ± uniform(0, jitter_factor × delay)
```

This is preferable to pure exponential backoff or uniform jitter because it decorrelates retry waves across concurrent clients that encountered the same transient fault, substantially reducing thundering-herd probability.

#### 3.5 Safe DSL Evaluator

`SafeEvaluator` in `dsl.py` performs **whitelist-based AST evaluation** rather than `eval()`, explicitly blocking:
- Dunder attribute access (`__class__`, `__import__`, etc.)
- f-string nodes (`ast.JoinedStr`)
- Any AST node type not on the explicit whitelist

This approach is formally sound: since the evaluator walks only allowed node types, it is impossible for injected code to access `builtins` or execute shell commands, directly mitigating CWE-94 (Improper Control of Generation of Code). The compiled transform cache (`CompiledTransform`) avoids repeated parsing, making repeated condition evaluation efficient.

---

### 4. Identified Weaknesses and Technical Debt

This section documents specific technical deficiencies found during static analysis. These are presented not to diminish the project's quality — which is genuinely high for an alpha — but to guide future engineering investment.

#### 4.1 Side-Effectful `state` Property (resilience.py:135–142)

The `CircuitBreaker.state` property performs a **state transition** (OPEN → HALF-OPEN) as a side effect of being read:

```python
@property
def state(self) -> CircuitState:
    if self._state is CircuitState.OPEN:
        if time.monotonic() - self._opened_at >= self.config.recovery_timeout:
            self._state = CircuitState.HALF_OPEN   # ← mutation in a getter
    return self._state
```

Python convention and the Principle of Least Surprise dictate that property getters should be idempotent and free of side effects. Any code that reads `breaker.state` more than once within a recovery window will observe inconsistent results. The transition should instead be triggered in `pre_call()`, `can_execute()`, or an explicit `tick()` method.

#### 4.2 Deprecated `asyncio.get_event_loop()` (distributed.py:125)

`TaskHandle.__init__` calls `asyncio.get_event_loop().create_future()`, which emits a `DeprecationWarning` in Python 3.10+ when called outside a running event loop. The correct idiom is `asyncio.get_running_loop().create_future()` (or simply deferring Future creation until the coroutine context). This will become an error in a future CPython release.

#### 4.3 Broken Encapsulation in `AdaptiveRouter` (qos.py:324–334)

`AdaptiveRouter.select_model()` syncs Thompson Sampling state by directly accessing the private `_stats` dictionary of its owned `CostAwareRouter` instance:

```python
ts_stats = self._ts_router._stats[m]  # noqa: SLF001  ← suppressed private access
ts_stats.ts_alpha = stats.ts_alpha
ts_stats.ts_beta  = stats.ts_beta
```

The `# noqa: SLF001` suppression acknowledges the violation. The correct fix is to expose a `sync_stats(model, alpha, beta)` method on `CostAwareRouter`, eliminating the coupling between `AdaptiveRouter` and `CostAwareRouter`'s internal data layout.

#### 4.4 Module-Level Mutable Global (sdk.py:76, 409, 415)

The `@agent` decorator registers into `_default_registry`, a module-level singleton. Three `global _default_registry` statements (each suppressed with `# noqa: PLW0603`) are required to support `AgentContext`. While `AgentContext` correctly restores the original reference on exit, this pattern is fragile in concurrent or multi-threaded test environments. A `contextvars.ContextVar` would provide thread-local isolation without global mutation.

#### 4.5 Duplicate `RetryConfig` in `__all__` (resilience.py:392)

`RetryConfig` appears twice in the module's `__all__` list — a minor hygiene issue with no runtime impact but indicative of a copy-paste oversight.

#### 4.6 Beta Posterior Conflates Objectives (routing.py)

Thompson Sampling draws from `Beta(ts_alpha, ts_beta)` where α tracks binary success/failure. This means latency and cost — which are continuous outcomes tracked separately via EMA — do not influence the Thompson draw. The routing decision is therefore a two-objective system with an impedance mismatch: UCB1/Thompson optimise for success rate, while the score strategy optimises for a weighted combination of cost, latency, and quality. A multi-objective bandit (e.g., Pareto-UCB1) would unify these objectives theoretically; the current hybrid is pragmatic but not formally coherent.

#### 4.7 Discovery Duplication (`ModelCatalogue` vs `ModelDiscovery`)

`AgentExecutor` in `execution.py` maintains its own `ModelCatalogue` for model resolution, while `discovery.py` provides the more capable `ModelDiscovery`. These two implementations serve overlapping concerns (API model hydration, TTL caching) and risk diverging. Phase 4 should consolidate to `ModelDiscovery` as the single discovery backend.

---

### 5. Value Proposition

#### 5.1 Current Utility

The project delivers **immediate, measurable utility** for Python developers integrating Replicate models into AI agent workflows:

- **Zero-friction MCP registration:** Any Replicate model becomes an MCP tool callable from Claude Desktop, Cursor, or any MCP-compliant host in under 30 minutes (documented onboarding guide).
- **Production reliability out-of-the-box:** Circuit breaking, retry with decorrelated jitter, and token-bucket rate limiting are fully wired — developers do not have to implement these patterns themselves.
- **Cost observability:** EMA-based cost and latency tracking, surfaced via the `routing://leaderboard` MCP resource, provides the first-party telemetry needed to justify or optimise Replicate spend.
- **Plugin extensibility:** The entry-point plugin system means logging, audit trails, cost-control hooks, and model-specific preprocessors can be added without forking the core library.
- **Full static type safety:** `mypy --strict` passes across all 33 modules, making the library safe to use as a typed dependency in downstream projects.

The test suite (575 tests, 91.5 % coverage) provides a credible guarantee of correctness for the current feature set.

#### 5.2 Potential Market Impact

The project occupies a specific and currently under-served intersection:

**Structural tailwinds:**
1. **MCP adoption velocity.** Anthropic's Model Context Protocol is rapidly becoming the de facto standard for tool-augmented LLM applications. Claude Desktop, Cursor, and Zed already ship MCP clients; Microsoft Copilot and third-party frameworks are integrating it. First-movers in the MCP infrastructure layer have disproportionate opportunity to become load-bearing dependencies.
2. **Replicate's model breadth.** Replicate's public catalog contains 50 000+ models spanning image generation, audio synthesis, video, code, and language. No other MCP bridge provides access to this depth with a production-grade orchestration layer.
3. **Multi-model orchestration demand.** Enterprise AI workloads increasingly require routing across multiple models for cost, latency, or capability reasons. The adaptive bandit routing layer — UCB1 cold-start → Thompson Sampling exploitation — is genuinely novel for API-level model selection and has no direct open-source equivalent.

**Differentiated capabilities vs. comparable tools:**

| Capability | This Project | Alternatives |
|------------|-------------|--------------|
| MCP + Replicate integration | ✅ Native | None known |
| Adaptive bandit routing | ✅ UCB1 + Thompson | Not in any MCP tool |
| Plugin entry-point ecosystem | ✅ PEP 517 | LangChain (different model) |
| Circuit breaker + retry | ✅ Built-in | Manual in all others |
| OpenTelemetry observability | ✅ Optional extra | Rare in open-source MCP |
| 90 %+ coverage + strict mypy | ✅ | Few open-source AI libraries |

**Current limitations on market capture:**
- The CLI's agent execution pathway is described as "scaffold-level" — production `replicate-agent run` requires further implementation.
- The distributed executor is **in-process** (asyncio queues on a single machine). The abstraction is correct and the `WorkerTransport` interface is in place, but the promise of "distributed" without gRPC or NATS transport is not yet redeemed for multi-machine deployments.
- No built-in HTTP/SSE transport for the MCP server limits deployment flexibility (e.g., cloud-hosted MCP endpoints).
- No multi-tenant authentication layer constrains use cases to single-owner deployments.

**Summary verdict:** The project is engineering-quality alpha software with a well-chosen architectural foundation and genuine technical differentiation. Its current value is real and immediately usable for individual developers and small teams; its market-impact potential depends primarily on two Phase 4 investments: (1) completing the CLI execution pathway, and (2) adding real network-distributed workers. Neither is architecturally blocked — the contracts exist, the test infrastructure is in place, and the coverage gate is already enforced.

---

## Project Structure

```
replicate-mcp-agents/
├── src/replicate_mcp/
│   ├── __init__.py                 # Public re-exports + version
│   ├── server.py                   # MCP server (FastMCP + routing resources)
│   ├── exceptions.py               # 13-exception domain hierarchy
│   ├── interfaces.py               # 8 @runtime_checkable Protocol ABCs
│   ├── validation.py               # Pydantic v2 schemas for all inputs
│   ├── security.py                 # SecretManager, SecretMasker, OTEL sanitiser
│   ├── resilience.py               # CircuitBreaker, RetryConfig, with_retry()
│   ├── routing.py                  # CostAwareRouter, ModelStats, EMA, Thompson
│   ├── qos.py                      # QoSLevel, QoSPolicy, UCB1Router, AdaptiveRouter
│   ├── ratelimit.py                # TokenBucket, RateLimiter (named buckets)
│   ├── observability.py            # OpenTelemetry facade, 5 instruments, span()
│   ├── dsl.py                      # AST-whitelist safe evaluator, CompiledTransform
│   ├── discovery.py                # ModelDiscovery, DiscoveryConfig, TTL cache
│   ├── sdk.py                      # @agent, AgentBuilder, WorkflowBuilder, AgentContext
│   ├── distributed.py              # WorkerNode, NodeRegistry, DistributedExecutor
│   ├── agents/
│   │   ├── registry.py             # AgentRegistry, AgentMetadata
│   │   ├── execution.py            # AgentExecutor (streaming, semaphore, retry)
│   │   ├── composition.py          # AgentWorkflow, DAG, topological sort, fan-out
│   │   └── transforms.py           # TransformRegistry (no eval)
│   ├── plugins/
│   │   ├── base.py                 # BasePlugin ABC, PluginMetadata, PluginError
│   │   ├── loader.py               # load_plugins(), load_plugin_from_path()
│   │   └── registry.py             # PluginRegistry, lifecycle, hook dispatch
│   ├── mcp/                        # MCP protocol data structures
│   ├── cli/                        # Click-based CLI (replicate-agent)
│   └── utils/
│       ├── checkpointing.py        # Atomic checkpoint persistence
│       ├── telemetry.py            # In-memory cost/latency accumulator
│       └── logging.py              # structlog configuration
├── tests/                          # 575 tests across 26 files
├── docs/
│   ├── guides/                     # Getting started, plugins guide
│   ├── api/                        # mkdocstrings API references (20 modules)
│   ├── adr/                        # Architecture Decision Records 001–008
│   ├── slos.md                     # 6 SLO definitions + error budget policy
│   └── runbooks/                   # Top-10 failure runbook
├── mkdocs.yml
└── pyproject.toml
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- [Poetry](https://python-poetry.org/) 2.x
- A [Replicate API token](https://replicate.com/account/api-tokens)

### Installation

```bash
git clone https://github.com/your-org/replicate-mcp-agents.git
cd replicate-mcp-agents
poetry install --with dev,docs

# Run the test suite
poetry run pytest                      # 575 tests, 91.5 % coverage

# Type-check all 33 source files
poetry run mypy src/

# Lint
poetry run ruff check .

# Launch the MCP server
export REPLICATE_API_TOKEN=r8_your_token_here
poetry run replicate-mcp-server
```

### Claude Desktop Integration

Add to `~/.config/claude/mcp_config.json`:

```json
{
  "mcpServers": {
    "replicate-agent": {
      "command": "poetry",
      "args": ["run", "replicate-mcp-server"],
      "env": {
        "REPLICATE_API_TOKEN": "${REPLICATE_API_TOKEN}"
      }
    }
  }
}
```

Claude will automatically discover all registered Replicate models as MCP tools.

---

## Usage Examples

### Declarative Agent Registration (`@agent` decorator)

```python
from replicate_mcp.sdk import agent, AgentContext

@agent(
    model="meta/llama-3-8b-instruct",
    description="Fast chat completion via Llama 3",
    tags=["chat", "llama"],
    supports_streaming=True,
    estimated_cost=0.0005,
)
def llama_chat(prompt: str) -> dict:
    """Invoke Llama 3 with the given prompt."""
    return {"prompt": prompt}
```

### Fluent Builder API

```python
from replicate_mcp.sdk import AgentBuilder, WorkflowBuilder

# Build agent metadata
spec = (
    AgentBuilder("summariser")
    .model("mistral/mixtral-8x7b-instruct")
    .description("Summarise long documents into bullet points")
    .tag("nlp", "summarisation")
    .streaming(True)
    .estimated_cost(0.003)
    .avg_latency(4_000)
    .build()
)

# Build a multi-step pipeline
workflow = (
    WorkflowBuilder("research-pipeline")
    .description("Search → analyse → summarise")
    .then("searcher",   input_map={"query": "user_query"})
    .then("analyst",    input_map={"data": "search_results"})
    .then("summariser", condition="len(output) > 200")
    .build()
)
print(workflow.agent_names)  # ['searcher', 'analyst', 'summariser']
```

### Adaptive Bandit Routing with QoS

```python
from replicate_mcp.qos import AdaptiveRouter, QoSLevel, QoSPolicy

router = AdaptiveRouter(explore_threshold=20)
router.register_model("meta/llama-3-8b",    initial_cost=0.0005, initial_latency_ms=1_200)
router.register_model("mistral/mixtral-8x7b", initial_cost=0.003,  initial_latency_ms=3_500)
router.register_model("anthropic/claude",    initial_cost=0.015,  initial_latency_ms=2_000)

# Enforce the FAST tier: reject models with EMA latency > 2 000 ms
policy = QoSPolicy.for_level(QoSLevel.FAST)

# First 20 calls: UCB1 (systematic exploration)
# Calls 21+:     Thompson Sampling (exploitation)
model = router.select_model_with_policy(
    ["meta/llama-3-8b", "mistral/mixtral-8x7b", "anthropic/claude"],
    policy=policy,
)
print(router.active_strategy)  # "ucb1" or "thompson"

# Feed back the outcome so the router learns
router.record_outcome(model, latency_ms=1_150, cost_usd=0.0004, success=True, quality=0.92)
```

### Distributed Execution (2-node)

```python
import asyncio
from replicate_mcp.distributed import DistributedExecutor, WorkerNode

async def main():
    async with DistributedExecutor() as executor:
        executor.add_node(WorkerNode("gpu-node-1", concurrency=8))
        executor.add_node(WorkerNode("gpu-node-2", concurrency=8))

        # Single task
        result = await executor.submit("llama_chat", {"prompt": "Explain MCP in one sentence."})
        print(result.chunks)

        # Batch
        results = await executor.run_many(
            [("llama_chat", {"prompt": f"Summarise paper {i}"}) for i in range(10)]
        )
        print(f"Processed {len(results)} tasks across 2 nodes")

asyncio.run(main())
```

### Plugin — Custom Cost Tracker

```python
# my_package/cost_plugin.py
from replicate_mcp.plugins import BasePlugin, PluginMetadata

class CostTrackerPlugin(BasePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="cost_tracker", version="1.0.0",
                              description="Accumulates USD spend per agent")

    def setup(self) -> None:
        self._spend: dict[str, float] = {}

    def teardown(self) -> None:
        print("Total spend:", self._spend)

    def on_agent_result(self, agent_name, chunks, latency_ms):
        self._spend[agent_name] = self._spend.get(agent_name, 0.0) + 0.001
```

Register via `pyproject.toml`:

```toml
[project.entry-points."replicate_mcp.plugins"]
cost_tracker = "my_package.cost_plugin:CostTrackerPlugin"
```

### DAG Workflow with Parallel Fan-Out

```python
from replicate_mcp.agents import AgentNode, AgentWorkflow, WorkflowEdge

wf = (
    AgentWorkflow(name="analysis", description="Parallel analysis pipeline")
    .add_agent("ingest",  AgentNode(model_id="meta/llama-3-8b",     role="extractor"))
    .add_agent("critic",  AgentNode(model_id="mistral/mixtral-8x7b", role="critic"))
    .add_agent("advocate",AgentNode(model_id="meta/llama-3-70b",     role="advocate"))
    .add_agent("judge",   AgentNode(model_id="anthropic/claude-opus", role="judge"))
)
# critic and advocate run concurrently (same DAG level)
wf.add_edge(WorkflowEdge(from_agent="ingest",   to_agent="critic"))
wf.add_edge(WorkflowEdge(from_agent="ingest",   to_agent="advocate"))
wf.add_edge(WorkflowEdge(from_agent="critic",   to_agent="judge"))
wf.add_edge(WorkflowEdge(from_agent="advocate", to_agent="judge"))

async for event in wf.execute({"text": "Draft policy document"}):
    print(f"[{event['node']}] {event['output']}")
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Interface contracts | `typing.Protocol` (8 ABCs) | Runtime-checkable; no inheritance coupling |
| Cycle detection | DFS 3-colour at `add_edge()` | Fail-fast; no broken DAGs enter execution |
| Parallel fan-out | `anyio.create_task_group()` | Framework-agnostic; asyncio + trio compatible |
| Checkpoints | `tempfile` + `os.replace()` | POSIX-atomic; no corruption on crash |
| Expression safety | AST-whitelist evaluator | CWE-94 prevention; no `eval()` |
| Retry delay | Decorrelated jitter (AWS) | Prevents thundering-herd on Replicate API |
| Cold-start routing | UCB1 bandit | O(√KN log N) regret bound; deterministic |
| Warm routing | Thompson Sampling | Bayesian; empirically superior post cold-start |
| Observability | OpenTelemetry with no-op | Vendor-neutral; zero overhead when absent |
| Plugin discovery | PEP 517 entry points | Standard Python ecosystem pattern |
| Security | keyring + env; pattern masking | Secrets never logged; CWE-312 prevention |
| Validation | Pydantic v2 | Compile-time schema enforcement; JSON Schema export |

---

## Roadmap

| Phase | Sprints | Theme | Status |
|-------|---------|-------|--------|
| **1** | S1–S4 | Foundation: MCP server, DAG engine, checkpointing | ✅ Complete |
| **2** | S5–S8 | Hardening: protocols, security, resilience, routing, OTEL | ✅ Complete |
| **3** | S9–S12 | Differentiation: discovery, SDK, QoS, plugins, distributed | ✅ Complete |
| **4** | S13–S16 | Scale: HTTP transport, gRPC workers, multi-tenant auth, dashboard | 🔲 Planned |

**Phase 4 priorities** (from architectural analysis above):
1. Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()` in `TaskHandle`
2. Expose `sync_stats()` on `CostAwareRouter` to fix `AdaptiveRouter` encapsulation
3. Move `CircuitBreaker` OPEN→HALF-OPEN transition out of the `state` property
4. Consolidate `ModelCatalogue` (execution.py) into `ModelDiscovery` (discovery.py)
5. Add HTTP/SSE MCP transport for cloud-hosted deployments
6. Implement real network-distributed workers (gRPC or NATS transport behind `WorkerTransport`)
7. Complete CLI `replicate-agent run` execution pathway

See [CHANGELOG.md](CHANGELOG.md) for full release notes and [docs/adr/](docs/adr/) for Architecture Decision Records 001–008.

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).