# ADR-006 · Fluent SDK and @agent Decorator Design

**Date:** 2025-01-01  
**Status:** Accepted  
**Deciders:** Platform Team  
**Scope:** E12 (Sprint S9)

---

## Context

Users need an ergonomic Python API for defining Replicate-backed agents without
writing boilerplate registry code.  The options considered were:

1. **Decorator-only** — `@agent` registers the function via side-effect.
2. **Builder-only** — `AgentBuilder("name").model(...).build()`.
3. **Both (chosen)** — decorator for quick declarations; builder for programmatic
   construction where metadata comes from runtime data.

## Decision

Implement both patterns sharing the same `AgentRegistry` backend:

- **`@agent`** — bare form (`@agent`) and parameterised form (`@agent(model=...)`).
  Preserves the decorated callable unchanged; registration is the only side-effect.
- **`AgentBuilder`** — fluent method-chaining builder that never modifies global
  state until `.register()` is explicitly called.
- **`WorkflowBuilder`** — sequential pipeline builder producing an immutable
  `WorkflowSpec`.
- **`AgentContext`** — context manager that swaps the module-level registry so
  tests can use `@agent` without polluting the global state.

### v0.7.0 Update: Lazy Initialization

The module-level registries (`_default_registry`, `_workflow_registry`) are now
**lazily initialized** (created on first access, not at import time). This
eliminates mutable global state at module load time and removes the need for
`global` statements during normal operation. The `reset_*()` functions now set
registries to `None`, triggering re-initialization on next access.

## Consequences

| Positive | Negative |
|---|---|
| IDE auto-complete works on builder methods | Two APIs to document and maintain |
| `@agent` reads naturally for declarative code | `AgentContext` is unusual boilerplate for tests |
| `WorkflowSpec` is immutable — safe to pass around | No async builder support yet |
| Lazy initialization eliminates eager global state | None — `reset_default_registry()` is now safe (sets to `None`) |
