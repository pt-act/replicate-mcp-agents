# ADR-012: Cache Eviction Policy

**Status**: Accepted  
**Date**: 2026-04-29  
**Version**: v0.8.0 Feature #25  
**Author**: Orion-OS Agent

---

## Context

The `ResultCache` in `replicate-mcp-agents` provides content-addressed result caching to eliminate redundant API calls during development. However, the original implementation only supported LRU (Least Recently Used) eviction. Different workloads have different access patterns:

- **Temporal locality workloads** (e.g., iterative model tuning) — LRU is optimal
- **Streaming workloads** (e.g., real-time inference) — FIFO is better (newer = more valuable)
- **Freshness-critical workloads** (e.g., dynamic content) — TTL-only is appropriate
- **Frequency-based workloads** (e.g., common queries) — LFU would be ideal

Additionally, the cache lacked:
- Thread safety for concurrent access
- Background cleanup of expired entries
- Detailed eviction statistics

## Decision

Implement **configurable cache eviction policies** in `ResultCache`:

1. **Four eviction policies**:
   - `LRU` (default) — Least Recently Used
   - `TTL` — Time-To-Live only (no capacity-based eviction)
   - `FIFO` — First In, First Out
   - `LFU` — Least Frequently Used (reserved for future)

2. **Thread safety** — All cache operations protected by `threading.Lock`

3. **Background eviction** — Optional periodic cleanup of expired entries

4. **Enhanced statistics** — Track evictions, policy-specific metrics

## Architecture

### Eviction Policy Enum

```python
class EvictionPolicy(Enum):
    LRU = "lru"      # Least Recently Used (default)
    TTL = "ttl"      # Time-To-Live only
    FIFO = "fifo"    # First In, First Out
    LFU = "lfu"      # Least Frequently Used (reserved)
```

### Policy Selection Guide

| Policy | Eviction Criteria | Best For | Memory Growth |
|--------|-------------------|----------|---------------|
| `LRU` | Least recent access | Temporal locality, iterative dev | Bounded |
| `TTL` | Expiration time only | Freshness-critical, dynamic data | Unbounded* |
| `FIFO` | Oldest insertion | Streaming, newer-is-better | Bounded |
| `LFU` | Least frequent access | Hot-spot caching (future) | Bounded |

\* TTL policy only evicts expired entries. If no entries expire, cache may exceed `max_entries`.

### Cache Entry Structure

```python
@dataclass
class _CacheEntry:
    chunks: list[dict[str, Any]]    # Cached response chunks
    expires_at: float              # TTL expiration timestamp
    inserted_at: float             # For FIFO ordering
    access_count: int              # For LFU policy (future)
    last_accessed: float           # For LRU ordering
```

### Eviction Algorithms

#### LRU (Least Recently Used)

```python
# On access: move to end (most recently used)
self._store.move_to_end(key)

# On capacity: evict first (least recently used)
evicted_key, _ = self._store.popitem(last=False)
```

**Rationale**: Most recent access is the best predictor of next access for many workloads.

#### TTL (Time-To-Live Only)

```python
# Only evict expired entries during put
while at_capacity and policy == TTL:
    evicted = evict_expired()
    if evicted == 0:
        # No expired entries, allow growth
        return
```

**Rationale**: If freshness is the only eviction criterion, capacity limits should not force premature eviction.

#### FIFO (First In, First Out)

```python
# Evict oldest by insertion time
oldest_key = min(self._store.keys(),
                 key=lambda k: self._store[k].inserted_at)
del self._store[oldest_key]
```

**Rationale**: In streaming workloads, newer data is always more valuable regardless of access patterns.

## Usage

### Basic LRU (Default)

```python
from replicate_mcp import ResultCache

# LRU is the default
cache = ResultCache(ttl_s=300, max_entries=500)
```

### FIFO for Streaming Workloads

```python
from replicate_mcp import ResultCache, EvictionPolicy

# FIFO: newer entries always preferred
cache = ResultCache(
    ttl_s=60,
    max_entries=100,
    policy=EvictionPolicy.FIFO,
)
```

### TTL-Only for Freshness

```python
from replicate_mcp import ResultCache, EvictionPolicy

# TTL: only expired entries are evicted
# Cache may grow beyond max_entries if nothing expires
cache = ResultCache(
    ttl_s=30,
    max_entries=1000,
    policy=EvictionPolicy.TTL,
)
```

### With Background Eviction

```python
from replicate_mcp import ResultCache

# Background thread periodically cleans up expired entries
cache = ResultCache(
    ttl_s=300,
    max_entries=500,
    background_eviction=True,
    background_interval_s=60.0,  # Every minute
)

# ... use cache ...

# Clean shutdown (optional, also happens in __del__)
cache.stop_background_eviction()
```

### Access Statistics

```python
cache = ResultCache(ttl_s=300)

# ... use cache ...

stats = cache.stats
print(f"Size: {stats['size']}/{stats['capacity']}")
print(f"Hit rate: {stats['hit_rate']:.1%}")
print(f"Evictions: {stats['evictions']}")
print(f"Policy: {stats['policy']}")
```

## Thread Safety

All cache operations are protected by a `threading.Lock`:

```python
with self._lock:
    entry = self._store.get(key)
    if entry is None:
        self._misses += 1
        return None
    # ... process hit ...
```

This enables safe concurrent access from multiple threads in multi-worker deployments.

## Background Eviction

Optional background thread for periodic cleanup:

```
┌─────────────────────────────────────┐
│  Background Thread (daemon)        │
│                                      │
│  while not stopped:                 │
│      sleep(interval)                │
│      evict_expired()                │
│      log.debug("evicted N entries") │
│                                      │
└─────────────────────────────────────┘
```

**Benefits**:
- Prevents memory leaks from expired entries
- Non-blocking — doesn't delay foreground operations
- Optional — disabled by default (zero overhead)

**Trade-off**: Additional thread and periodic CPU usage.

## API Reference

### EvictionPolicy

```python
class EvictionPolicy(Enum):
    """Cache eviction policy controlling how entries are removed."""

    LRU = "lru"   # Least Recently Used (default)
    TTL = "ttl"   # Time-To-Live only
    FIFO = "fifo" # First In, First Out
    LFU = "lfu"   # Least Frequently Used (reserved)
```

### ResultCache (extended)

```python
class ResultCache:
    def __init__(
        self,
        *,
        ttl_s: float = 300.0,
        max_entries: int = 500,
        policy: EvictionPolicy = EvictionPolicy.LRU,
        background_eviction: bool = False,
        background_interval_s: float = 60.0,
    ) -> None

    @property
    def policy(self) -> EvictionPolicy

    @property
    def evictions(self) -> int

    @property
    def stats(self) -> dict[str, Any]
    # Returns: size, capacity, hits, misses, hit_rate,
    #          evictions, policy, ttl_s

    def stop_background_eviction(self) -> None
```

## Trade-offs

### Memory Overhead (Per Entry)

Each cache entry now stores additional metadata:

| Field | Size | Purpose |
|-------|------|---------|
| `inserted_at` | 8 bytes | FIFO ordering |
| `access_count` | 8 bytes | LFU tracking |
| `last_accessed` | 8 bytes | LRU ordering |

**Total**: +24 bytes per entry. Negligible for typical chunk sizes (KB+).

### Lock Contention

Thread safety requires acquiring a lock on every operation:

- **Cost**: ~1-5μs per operation (uncontended)
- **Benefit**: Safe concurrent access
- **Mitigation**: Lock held only for dict operations, not for chunk copying

### TTL Policy Memory Growth

TTL policy may exceed `max_entries` if no entries expire:

- **Risk**: Unbounded memory growth for long-running processes
- **Mitigation**: Use background eviction, or set a hard memory limit externally
- **Alternative**: Combine TTL with a hard cap (not implemented — future work)

## Future Work

### LFU Implementation

Complete the reserved `LFU` policy:

```python
def _evict_lfu(self) -> str:
    """Evict least frequently accessed entry."""
    lfu_key = min(
        self._store.keys(),
        key=lambda k: self._store[k].access_count,
    )
    del self._store[lfu_key]
    return lfu_key
```

### Adaptive Policy

Auto-select policy based on observed hit rates:

```python
# Hypothetical future API
from replicate_mcp.cache import AdaptiveCache

cache = AdaptiveCache(
    policies=[EvictionPolicy.LRU, EvictionPolicy.FIFO],
    evaluation_window=1000,  # Measure hit rate over 1000 ops
)
```

### Memory-Bounded TTL

Hard memory cap for TTL policy:

```python
cache = ResultCache(
    policy=EvictionPolicy.TTL,
    max_memory_mb=100,  # Evict oldest entries if >100MB
)
```

## Testing

### Unit Tests (30)

- `TestEvictionPolicyEnum` — Policy enum values and defaults
- `TestCacheEntry` — Entry initialization with metadata
- `TestResultCacheConstructor` — Policy configuration
- `TestLRUEviction` — LRU eviction behavior
- `TestTTLEviction` — TTL-only eviction
- `TestFIFOEviction` — FIFO ordering
- `TestAccessCountTracking` — Access count metrics
- `TestLastAccessedTracking` — Last accessed timestamps
- `TestEvictionStats` — Statistics tracking
- `TestThreadSafety` — Concurrent access safety
- `TestCacheRepr` — String representation
- `TestRefreshExistingEntry` — TTL refresh behavior
- `TestEvictExpiredFunctionality` — Manual expiration
- `TestEdgeCases` — Boundary conditions

## References

- `src/replicate_mcp/cache.py` — Cache implementation
- `tests/unit/test_cache_eviction_policy.py` — Unit tests
- `docs/adr/006.md` — Original cache design

---

[Quantum_State: ACCEPTED]
