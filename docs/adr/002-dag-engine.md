# ADR-002: DAG Engine Design

## Status
Accepted (v0.2.0)

## Context
The workflow engine needed to support multi-agent pipelines with:
- Arbitrary DAG topologies (linear, fan-out, diamond, etc.)
- Cycle prevention to avoid infinite loops
- Concurrent execution of independent nodes
- Per-node checkpointing for crash recovery

## Decision

### Cycle Detection — DFS 3-Colour
We use DFS 3-colour cycle detection on every `add_edge()` call. This **fail-fast** approach ensures that no broken DAG can ever enter the system. If the edge would create a cycle, it is rejected with `CycleDetectedError` and the graph remains unchanged (rollback).

**Why not BFS?** DFS naturally detects back-edges. 3-colour avoids revisiting fully explored subtrees. The overhead per `add_edge()` is O(V+E), which is negligible for DAGs with <1000 nodes.

### Topological Sort — Kahn's Algorithm
We use Kahn's algorithm to produce **execution levels** (list of lists). Nodes within the same level have no inter-dependencies and can run concurrently. This naturally maps to `anyio.create_task_group()`.

### Concurrency — anyio
We chose `anyio` over raw `asyncio` for framework-agnostic compatibility. This allows the workflow engine to run under both `asyncio` and `trio`.

### Checkpointing — Atomic + Per-Level
After each level completes, results are atomically checkpointed using `tempfile` + `os.replace()`. On resume, completed levels are skipped.

## Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| NetworkX | Heavy dependency for simple DAG ops; overkill |
| Lazy cycle detection | Silent bugs from invalid graphs |
| Process-based parallelism | Overkill; most model calls are I/O-bound |
| Celery/Dramatiq | Too heavyweight for an SDK library |

## Consequences
- All workflow mutations are validated at insertion time
- Execution is deterministic (sorted levels for reproducibility)
- Checkpoint format includes version and metadata for future migrations