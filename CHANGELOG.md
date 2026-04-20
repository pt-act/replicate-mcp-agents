# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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