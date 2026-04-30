# ADR-010: Worker Circuit Breakers for Distributed Execution

**Status**: Accepted  
**Date**: 2026-04-29  
**Version**: v0.8.0 Feature #9  
**Author**: Orion-OS Agent

---

## Context

The distributed execution system (`DistributedExecutor`, `HttpWorkerTransport`, `RemoteWorkerNode`) routes tasks to worker nodes across multiple machines. However, workers can fail independently — network partitions, Replicate API errors, resource exhaustion — causing cascading failures if the coordinator continues routing to unhealthy nodes.

We need circuit breaker protection at the worker level, similar to what we have for the Replicate API, but extended to cover entire worker nodes.

## Decision

Implement **Worker Circuit Breakers** — a distributed circuit breaker pattern where:

1. Each worker node self-tracks its circuit state via `WorkerCircuitBreaker`
2. Health endpoints expose circuit state for coordinator inspection
3. Coordinators check circuit state before routing and fail over automatically
4. HALF-OPEN workers receive reduced traffic during recovery

## Architecture

### Component Overview

```
┌──────────────────┐         ┌──────────────────┐
│   Coordinator    │         │   Worker Node    │
│                  │         │                  │
│  Distributed     │         │  WorkerHttpApp   │
│  Executor        │         │  ├─ /health      │
│  ├─ Health check │◄────────┤  │  └─ circuit  │
│  ├─ Circuit      │         │  ├─ /execute    │
│  │   filter      │         │  │  ├─ pre_call │
│  └─ Failover     │         │  │  ├─ execute  │
│                  │         │  │  └─ record    │
│  RemoteWorkerNode│         │  │     outcome   │
│  ├─ Cached state │         │  └─ /metrics     │
│  ├─ is_open()    │         │                  │
│  └─ submit()     │         │  WorkerCircuit   │
│     ├─ check     │         │  Breaker         │
│     └─ reject    │         │  ├─ CLOSED       │
│        if OPEN   │         │  ├─ OPEN         │
│                  │         │  └─ HALF_OPEN    │
└──────────────────┘         └──────────────────┘
```

### Key Classes

#### `WorkerCircuitBreaker`

Extends `CircuitBreaker` with worker-specific tracking:

```python
class WorkerCircuitBreaker(CircuitBreaker):
    def record_failure(self) -> None:
        super().record_failure()
        self._last_failure_at = time.monotonic()  # Track for retry-in calc

    def get_state(self) -> WorkerCircuitState:
        return WorkerCircuitState.from_circuit_breaker(self, self._last_failure_at)
```

#### `WorkerCircuitState`

Serializable circuit state for HTTP endpoints:

```python
@dataclass
class WorkerCircuitState:
    state: str  # "closed", "open", "half_open"
    failure_count: int
    success_count: int
    last_failure_at: float | None
    recovery_timeout: float
    half_open_max_calls: int
    half_open_calls: int
    can_execute: bool

    def to_dict(self) -> dict[str, Any]: ...
```

#### `WorkerHttpApp` Extension

`/health` endpoint now includes circuit state:

```json
{
  "status": "healthy",
  "node_id": "worker-192.168.1.5:7999",
  "circuit": {
    "state": "half_open",
    "failure_count": 5,
    "success_count": 1,
    "last_failure_at": 1714392847.123,
    "recovery_timeout": 60.0,
    "half_open_max_calls": 3,
    "half_open_calls": 1,
    "can_execute": true
  }
}
```

When circuit is OPEN, `/health` returns HTTP 503 with circuit details.

#### `HttpWorkerTransport.get_circuit_state()`

Fetches and parses circuit state from worker health endpoint:

```python
async def get_circuit_state(self) -> WorkerCircuitState | None:
    response = await client.get(f"{self._base_url}/health")
    data = response.json()
    circuit_data = data.get("circuit")
    if circuit_data:
        return WorkerCircuitState(...circuit_data...)
```

#### `RemoteWorkerNode` Circuit Awareness

```python
class RemoteWorkerNode:
    async def check_circuit_state(self) -> WorkerCircuitState | None:
        """Fetch and cache circuit state from worker."""
        state = await self._transport.get_circuit_state()
        self._circuit_state = state
        return state

    def is_circuit_open(self) -> bool:
        """Check if cached circuit state indicates OPEN."""
        return self._circuit_state.state == "open" if self._circuit_state else False

    async def submit(self, ...) -> None:
        # Check circuit before dispatching
        circuit_state = await self.check_circuit_state()
        if circuit_state.state == "open":
            raise WorkerCircuitOpenError(self._node_id, circuit_state)
        # ... proceed with submission
```

#### `DistributedExecutor` Circuit-Aware Routing

Filters out OPEN circuits and penalizes HALF-OPEN:

```python
def _least_loaded_all(self) -> WorkerNode | RemoteWorkerNode | None:
    candidates = []
    for n in self._remote_nodes.values():
        if n.health == NodeHealth.UNHEALTHY:
            continue
        if isinstance(n, RemoteWorkerNode) and n.is_circuit_open():
            continue  # Skip OPEN circuits
        candidates.append(n)

    def effective_load(n):
        base = n.load
        if isinstance(n, RemoteWorkerNode) and n.is_circuit_half_open():
            return base * 1.5  # 50% penalty
        return base

    return min(candidates, key=effective_load)
```

## Usage

### Enable Worker Circuit Breaker (Default)

```python
from replicate_mcp.worker_server import serve_worker

# Circuit breaker enabled by default
await serve_worker(host="0.0.0.0", port=7999)
```

### Custom Circuit Configuration

```python
from replicate_mcp.resilience import CircuitBreakerConfig

config = CircuitBreakerConfig(
    failure_threshold=10,
    recovery_timeout=120.0,
    half_open_max_calls=5,
)

await serve_worker(
    host="0.0.0.0",
    port=7999,
    enable_circuit_breaker=True,
    circuit_config=config,
)
```

### Disable Circuit Breaker

```python
await serve_worker(
    host="0.0.0.0",
    port=7999,
    enable_circuit_breaker=False,
)
```

### Coordinator with Circuit-Aware Routing

```python
from replicate_mcp.distributed import (
    DistributedExecutor,
    HttpWorkerTransport,
    RemoteWorkerNode,
)

transport = HttpWorkerTransport("http://worker-1:7999")
node = RemoteWorkerNode("worker-1", transport=transport)

async with DistributedExecutor() as executor:
    executor.add_remote_node(node)
    # Routing automatically respects circuit state
    result = await executor.submit("agent", {"prompt": "hello"})
```

## Benefits

### Failure Isolation

When a worker experiences repeated failures (Replicate API errors, network issues), its circuit opens, preventing cascading failures across the cluster.

### Automatic Recovery

After `recovery_timeout`, the circuit transitions to HALF-OPEN. The coordinator reduces traffic to the recovering worker (50% load penalty) while allowing probe calls to verify health.

### Observable State

Circuit state is exposed via `/health` and `/metrics` endpoints, enabling monitoring and alerting:

- `circuit.state: open` → Alert on-call
- `circuit.half_open_calls > 0` → Recovery in progress
- `circuit.failure_count` → Trend analysis

### Zero Configuration (Opt-Out)

Circuit breakers are enabled by default with sensible defaults. Users can customize or disable as needed.

## Trade-offs

### Additional Latency

Each `RemoteWorkerNode.submit()` now makes a `/health` request to check circuit state. This adds ~1-5ms per routing decision.

**Mitigation**: Circuit state is cached; we could implement background refresh for high-throughput scenarios.

### State Synchronization

The coordinator's cached circuit state may be stale if the worker's circuit transitions between the health check and task submission.

**Mitigation**: The worker's `/execute` endpoint also checks its own circuit and returns 503 if OPEN, causing the coordinator to retry/failover.

### HALF-OPEN Penalty Heuristic

The 50% load penalty for HALF-OPEN nodes is a heuristic. It may be too aggressive (slowing recovery) or too lenient (overloading recovering nodes) depending on workload characteristics.

**Mitigation**: Users can tune via `half_open_max_calls` in `CircuitBreakerConfig`.

## Testing

### Unit Tests (14)

- `test_worker_circuit_breaker.py` covers:
  - `WorkerCircuitState` serialization
  - `WorkerCircuitBreaker` timestamp tracking
  - `WorkerCircuitOpenError` exception handling
  - Configuration propagation

### Integration Tests (17)

- `test_worker_circuit_breaker_integration.py` covers:
  - Worker HTTP app circuit integration
  - Transport circuit state fetching
  - Remote worker node circuit checks
  - Distributed executor circuit-aware routing

### Property-Based Tests

```python
# Circuit state invariant: once OPEN, can_execute is False
def test_open_circuit_cannot_execute(state):
    if state.state == "open":
        assert not state.can_execute
```

## References

- [resilience.py](../src/replicate_mcp/resilience.py) — Core CircuitBreaker
- [worker_circuit_breaker.py](../src/replicate_mcp/worker_circuit_breaker.py) — Worker extension
- [worker_server.py](../src/replicate_mcp/worker_server.py) — Worker HTTP app
- [distributed.py](../src/replicate_mcp/distributed.py) — Coordinator routing

---

[Quantum_State: ACCEPTED]
