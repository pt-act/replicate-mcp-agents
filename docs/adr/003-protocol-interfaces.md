# ADR-003 · Protocol Interfaces for All Subsystems

**Status:** Accepted  
**Sprint:** S5  
**Date:** 2026-04-20  
**Deciders:** Eng Lead, Tech Lead  

---

## Context

Phase 1 delivered concrete classes (`AgentExecutor`, `AgentRegistry`,
`CheckpointManager`, `TelemetryTracker`) that worked in isolation but were
tightly coupled to each other.  Consumers imported concrete types directly,
making it impossible to:

- Swap implementations (e.g. a Redis-backed checkpoint store).
- Inject mocks in integration tests without monkey-patching.
- Enforce interface contracts at type-check time.

We considered three approaches:

| Approach | Pros | Cons |
|----------|------|------|
| Abstract Base Classes (`abc.ABC`) | Familiar, explicit | Forces inheritance; harder to adapt third-party types |
| `typing.Protocol` (structural typing) | Duck-typing compatible; no inheritance required | Less discoverable; need `@runtime_checkable` for `isinstance` |
| Pydantic models everywhere | Rich validation | Overhead; wrong tool for behaviour contracts |

## Decision

Adopt **`typing.Protocol` with `@runtime_checkable`** for all subsystem
interfaces, codified in `src/replicate_mcp/interfaces.py`.

Protocols defined:

| Protocol | Implemented by |
|----------|---------------|
| `AgentExecutorProtocol` | `AgentExecutor` |
| `AgentRegistryProtocol` | `AgentRegistry` |
| `CheckpointManagerProtocol` | `CheckpointManager` |
| `TelemetryTrackerProtocol` | `TelemetryTracker` |
| `ModelRouterProtocol` | `CostAwareRouter` |
| `CircuitBreakerProtocol` | `CircuitBreaker` |
| `RateLimiterProtocol` | `TokenBucket` |
| `ObservabilityProtocol` | `Observability` |

All public function signatures that accept subsystem objects must use the
Protocol type rather than the concrete class.

## Consequences

**Positive:**
- Concrete implementations can be swapped with zero changes to consumers.
- Tests can inject plain duck-typed fakes without monkey-patching.
- `isinstance(obj, AgentExecutorProtocol)` works at runtime.
- mypy enforces conformance structurally.

**Negative:**
- Additional file to maintain.
- `@runtime_checkable` Protocol `isinstance` checks are O(n) in the number
  of methods; not suitable for hot-loop usage.

**Risks & Mitigations:**
- Protocol drift if implementations add methods not in the Protocol:
  mitigated by CI mypy check (`disallow_untyped_defs = true`).