# ADR-010: v0.8.0 Feature Implementation — Worker Circuit Breakers, Version Pinning, Cache Eviction

**Status**: Proposed  
**Date**: 2026-04-28  
**Version**: 0.8.0

---

## Context

The next feature release (v0.8.0) targets three production-hardening capabilities identified in the ideas list:

1. **#9 — HTTP Worker Circuit Breakers**: Extend circuit breaker protection to distributed worker nodes
2. **#18 — Model Version Pinning**: Prevent accidental model version changes during discovery refresh
3. **#25 — Cache Eviction Policy**: Pluggable eviction strategies beyond LRU

---

## 1. HTTP Worker Circuit Breakers (#9)

### Current State
- `CircuitBreaker` exists in `resilience.py` for Replicate API calls
- `WorkerHttpApp` in `worker_server.py` has health/metrics endpoints but no circuit breaker state
- `RemoteWorkerNode` checks health but has no failure tracking

### Proposal

Add circuit breaker state to worker nodes so the coordinator can:
- Track worker failure rates
- Automatically remove unhealthy workers from rotation
- Gradually reintroduce recovered workers (HALF-OPEN → CLOSED)

### Implementation

```python
# New: WorkerCircuitBreaker in worker_server.py
@dataclass
class WorkerCircuitState:
    """Circuit breaker state exposed via /health endpoint."""
    state: CircuitState  # CLOSED, OPEN, HALF_OPEN
    failure_count: int
    success_count: int
    last_failure_at: float | None
    recovery_timeout: float

# Extend WorkerHttpApp
class WorkerHttpApp:
    def __init__(...):
        self.circuit_breaker = CircuitBreaker(
            name=f"worker-{host}-{port}",
            config=CircuitBreakerConfig(...)
        )
    
    async def execute_endpoint(self, request):
        # Wrap execution in circuit breaker
        async with self.circuit_breaker.context():
            return await self._execute(request)
    
    async def health_endpoint(self, request):
        return JSONResponse({
            "status": "healthy",
            "circuit": self.circuit_breaker.to_dict(),
            "load": self.metrics.current_load()
        })
```

### Coordinator Integration

```python
# RemoteWorkerNode checks circuit state before routing
async def execute(self, agent_input: AgentInput) -> AsyncIterator[dict]:
    # Fetch current circuit state from worker health endpoint
    circuit_state = await self._fetch_circuit_state()
    
    if circuit_state.state == CircuitState.OPEN:
        raise WorkerUnavailableError(f"Worker {self.url} circuit is OPEN")
    
    if circuit_state.state == CircuitState.HALF_OPEN:
        # Limit probe traffic to HALF_OPEN workers
        if not self._should_probe():
            raise WorkerUnavailableError(...)
    
    # Proceed with execution
    return await self._execute(agent_input)
```

---

## 2. Model Version Pinning (#18)

### Current State
- `ModelDiscovery` supports versions in format `owner/model:version`
- `DiscoveryConfig` has `ttl_seconds` but no version pinning
- Background refresh can update to latest version unexpectedly

### Proposal

Allow users to pin specific model versions, preventing automatic updates during refresh cycles.

### Implementation

```python
# Extended DiscoveryConfig
@dataclass
class DiscoveryConfig:
    # ... existing fields ...
    version_pinning: VersionPinningMode = VersionPinningMode.LATEST
    pinned_versions: dict[str, str] = field(default_factory=dict)
    # e.g., {"meta/llama-2-70b": "5c7854e8"}

class VersionPinningMode(Enum):
    LATEST = "latest"        # Always use latest (current behavior)
    MAJOR = "major"          # Pin to major version, allow minor/patch
    EXACT = "exact"          # Pin to exact version hash

# ModelDiscovery.refresh() modification
async def refresh(self) -> None:
    models = await self._fetch_models()
    
    for model in models:
        model_id = model["owner"] + "/" + model["name"]
        
        if model_id in self.config.pinned_versions:
            # Skip refresh for pinned models
            pinned_version = self.config.pinned_versions[model_id]
            if not self._version_matches(model, pinned_version):
                logger.info(f"Skipping refresh for pinned model {model_id}")
                continue
        
        # ... normal registration logic ...
```

---

## 3. Cache Eviction Policy (#25)

### Current State
- `ResultCache` uses LRU (Least Recently Used) eviction
- TTL-based expiration exists but is separate from eviction
- No pluggable eviction strategies

### Proposal

Make eviction policy pluggable with support for:
- **LRU** (current): Evict least recently accessed
- **LFU**: Evict least frequently accessed
- **TTL-priority**: Evict entries closest to expiration
- **Weighted**: Combine access patterns with entry size/cost

### Implementation

```python
# New: EvictionPolicy protocol
class EvictionPolicy(Protocol):
    """Protocol for cache eviction strategies."""
    
    def on_access(self, key: str, entry: CacheEntry) -> None:
        """Called when entry is accessed."""
        ...
    
    def on_insert(self, key: str, entry: CacheEntry) -> None:
        """Called when entry is inserted."""
        ...
    
    def select_victim(self, candidates: list[tuple[str, CacheEntry]]) -> str:
        """Select key to evict from candidates."""
        ...

# LFU implementation
class LFUEvictionPolicy:
    def __init__(self):
        self.access_counts: dict[str, int] = {}
    
    def on_access(self, key: str, entry: CacheEntry) -> None:
        self.access_counts[key] = self.access_counts.get(key, 0) + 1
    
    def select_victim(self, candidates: list[tuple[str, CacheEntry]]) -> str:
        return min(candidates, key=lambda c: self.access_counts.get(c[0], 0))[0]

# Extended ResultCache
class ResultCache:
    def __init__(
        self,
        ttl_s: float = 300.0,
        max_entries: int = 1000,
        eviction_policy: EvictionPolicy | None = None
    ):
        self.eviction_policy = eviction_policy or LRUEvictionPolicy()
        # ... existing init ...
```

---

## Implementation Order

### Recommended Sequence:

1. **#9 Worker Circuit Breakers** (highest production impact)
   - Extends existing CircuitBreaker pattern
   - Critical for distributed reliability
   - ~2-3 days

2. **#18 Model Version Pinning** (medium complexity)
   - Configuration extension
   - Important for reproducibility
   - ~1-2 days

3. **#25 Cache Eviction Policy** (lowest priority)
   - Performance optimization
   - Nice-to-have for specialized workloads
   - ~2-3 days

### Dependencies

- #9 depends on existing `CircuitBreaker` in `resilience.py`
- #18 depends on `ModelDiscovery` in `discovery.py`
- #25 is self-contained in `cache.py`

---

## Testing Strategy

### #9 Worker Circuit Breakers
- Unit: Mock worker health endpoint, verify state transitions
- Integration: Start/stop workers, verify circuit opens/closes
- E2E: Kill worker mid-request, verify coordinator fails over

### #18 Version Pinning
- Unit: Mock API responses with different versions
- Integration: Background refresh with pinned versions
- E2E: Full discovery cycle with version assertions

### #25 Eviction Policies
- Unit: Synthetic access patterns, verify victim selection
- Property-based: Eviction invariants (monotonicity, completeness)

---

## Open Questions

1. **Worker Circuit Breakers**: Should circuit state be shared across coordinator processes, or per-process?
2. **Version Pinning**: How to handle version hash format changes in Replicate API?
3. **Eviction Policies**: Should we expose metrics (hit rate, eviction count) per policy?

---

## Decision

Implement in order #9 → #18 → #25, with full test coverage for each.

**Next Step**: Begin implementation of #9 Worker Circuit Breakers.

---

[Quantum_State: PROPOSED]
