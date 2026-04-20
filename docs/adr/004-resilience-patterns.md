# ADR-004 · Resilience Patterns: Circuit Breaker + Retry

**Status:** Accepted  
**Sprint:** S6  
**Date:** 2026-04-20  
**Deciders:** Eng Lead, SRE  

---

## Context

Phase 1's `AgentExecutor` had a hand-rolled retry loop with decorrelated
jitter.  It lacked:

- **Circuit breaking** — if Replicate is down, retries amplify traffic.
- **Half-open probing** — no way to auto-recover without operator intervention.
- **Structured retry config** — hard-coded constants scattered through code.
- **Composability** — retry + circuit breaker were not independently testable.

The Replicate API has rate limits and occasional elevated latency.  Without
circuit breaking, a cascade of `429 Too Many Requests` responses would cause
all concurrent workflows to pile up retries, worsening the situation.

## Decision

Implement `src/replicate_mcp/resilience.py` with:

### Circuit Breaker — three-state FSM

```
CLOSED ──(failures ≥ threshold)──► OPEN
OPEN   ──(recovery_timeout)──────► HALF_OPEN
HALF_OPEN ──(probes succeed)─────► CLOSED
HALF_OPEN ──(probe fails)────────► OPEN
```

Defaults: `failure_threshold=5`, `recovery_timeout=60s`,
`half_open_max_calls=3`, `success_threshold=2`.

### Retry — `RetryConfig` + `compute_retry_delay`

Decorrelated jitter formula (AWS Architecture Blog):
```
delay = min(max_delay, base * 2^attempt) ± jitter_factor * delay
```

Defaults: `max_retries=3`, `base_delay=0.5s`, `max_delay=30s`,
`jitter_factor=0.25`.

### Composition

`with_retry(fn, config, breaker)` composes both.  The circuit breaker is
checked *before* each attempt; outcomes are reported *after*.  Callers
never need to coordinate the two independently.

`retry_iter(fn, config, breaker)` provides the same guarantee for
async-generator streaming calls.

### Integration

`AgentExecutor` now holds a per-model `CircuitBreaker` dict and a shared
`RetryConfig`.  The executor wires both into every `run()` call.

## Consequences

**Positive:**
- Self-healing under transient Replicate outages.
- Protection against cascade amplification.
- Independent testability of both patterns.
- Observable state (`breaker.state`, `breaker.failure_count`).

**Negative:**
- Failed calls take longer to surface errors in OPEN state (fast-fail
  instead of waiting, but callers must handle `CircuitOpenError`).
- HALF_OPEN probe window is a small window of potential latency spike.

**Risks & Mitigations:**
- `recovery_timeout` too short → premature recovery → re-trip.
  Mitigated by default of 60s and operator tunability via `CircuitBreakerConfig`.
- Circuit breakers are per-executor instance.  Distributed deployments
  need a shared state store (Redis) — deferred to Phase 3.