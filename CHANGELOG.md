# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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