# ADR-007 · Plugin System Design (Entry Points)

**Date:** 2025-01-01  
**Status:** Accepted  
**Deciders:** Platform Team  
**Scope:** E14 (Sprint S10)

---

## Context

We need a way for third-party packages to extend replicate-mcp-agents with custom
logic (logging adapters, cost trackers, model post-processors, etc.) without
modifying the core package.

Options evaluated:

1. **Configuration file** (`plugins.yaml`) — simple but not pip-installable.
2. **Entry points (chosen)** — standard Python packaging mechanism; plugins are
   installable via `pip install my-plugin`.
3. **Import hooks** — fragile; incompatible with virtualenvs.

## Decision

Use Python **entry points** under the group `replicate_mcp.plugins`.

Plugin authors declare their class in `pyproject.toml`:

```toml
[project.entry-points."replicate_mcp.plugins"]
my_plugin = "my_package.plugin:MyPlugin"
```

The `load_plugins()` function discovers and instantiates all registered classes
via `importlib.metadata.entry_points()`.  Plugins must subclass `BasePlugin`
and implement `setup()`, `teardown()`, and optionally hook methods
(`on_agent_run`, `on_agent_result`, `on_error`).

`PluginRegistry` manages lifecycle and dispatches hooks; hook errors are
caught and logged so a buggy plugin cannot crash the executor.

## Consequences

| Positive | Negative |
|---|---|
| `pip install my-plugin` just works | Entry-point scanning is slightly slow at startup |
| Zero coupling between plugin and core | Plugin authors must ship a proper Python package |
| Hooks are isolated — one plugin cannot affect another | No dependency injection into plugins yet |
| `PluginRegistry` provides clean lifecycle management | `teardown()` errors are swallowed (logged only) |
