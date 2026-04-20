# ADR-008 · Distributed Execution Model

**Date:** 2025-01-01  
**Status:** Accepted  
**Deciders:** Platform Team  
**Scope:** E15 (Sprint S11)

---

## Context

Phase 3 exit criterion (5) requires "2-node distributed execution".  Options:

1. **Single asyncio event loop with task concurrency** — simple but single-process.
2. **multiprocessing** — true parallelism but complex IPC and no shared memory.
3. **asyncio task-queue workers (chosen)** — in-process, asyncio-native; transport
   can be swapped for gRPC/HTTP in a future release without changing the public API.
4. **Celery/RQ** — heavyweight; adds a Redis dependency.

## Decision

Implement an in-process, asyncio-native execution layer:

- **`WorkerNode`** — owns an `asyncio.Queue`; configurable concurrency via a
  pool of worker coroutines (`asyncio.create_task` per worker).
- **`NodeRegistry`** — maintains the pool; `least_loaded()` returns the node
  with the lowest combined active+queued task count (load-based routing).
- **`DistributedExecutor`** — accepts task submissions, routes to
  `least_loaded()`, handles failover when a node is overloaded.
- **`TaskHandle`** — wraps `asyncio.Future` so callers can `await` for results.

Transport abstraction is designed into the `WorkerNode._execute` method:
injecting a real `AgentExecutor` via `node._executor` gives production
behaviour; leaving it unset activates a stub (used in tests).

## Consequences

| Positive | Negative |
|---|---|
| Zero external dependencies | In-process only (no cross-host distribution yet) |
| `asyncio.Queue` back-pressure is built-in | GIL limits CPU-bound parallelism |
| `TaskHandle` is awaitable — natural async ergonomics | Node state is not persisted across restarts |
| Failover on overload without dropping tasks | Health check is passive (task failures) not active (heartbeats) |
| Transport can be swapped without API change | gRPC/HTTP transport is out of scope for Phase 3 |
