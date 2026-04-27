# ADR-001: Dependency Cleanup — Pydantic, Typer, MCP SDK

**Status:** Accepted  
**Date:** 2026-04-20  
**Deciders:** Engineering team  

## Context

The v0.1.0 scaffold declared several dependencies that were unused or redundant:

1. **Pydantic v2** — declared as a core dependency but never imported anywhere in the codebase.
2. **Typer** — declared as an optional `cli-extras` dependency, but the CLI is built entirely with Click. Since Typer wraps Click, having both creates architectural ambiguity.
3. **MCP Python SDK** — declared only as a Poetry dev-group dependency pointing at a GitHub repo, never imported. The server.py file was a no-op stub.

## Decision

### Pydantic v2 → **Keep and adopt**
- Pydantic v2 will be used for all external-facing data validation (API inputs, YAML workflow schemas, CLI payloads).
- Internal dataclasses remain as-is for performance in hot paths (telemetry, composition).
- The `pydantic.mypy` plugin is already configured.

### Typer → **Remove**
- The `cli-extras` optional dependency group containing `typer` is removed.
- Click is the sole CLI framework. It is stable, well-tested, and already fully integrated.
- This eliminates the architectural ambiguity of having two CLI frameworks in the dependency tree.

### MCP Python SDK → **Promote to core dependency**
- `mcp (>=1.20.0,<2.0.0)` is now a core runtime dependency (not a dev-only group).
- The git-pinned dev dependency is removed.
- The server.py module now imports and uses the SDK's `FastMCP` class.
- A mypy override for `mcp.*` is added to handle missing type stubs.

## Consequences

- Zero phantom dependencies: every declared dependency is now imported.
- `poetry install` is faster (no git clone for MCP SDK).
- The CLI has a single, unambiguous framework (Click).
- Pydantic adoption will be incremental — starting with workflow YAML validation in Phase 2.