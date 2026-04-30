"""Content-addressed result cache for Replicate agent invocations.

Eliminates redundant API calls during development by caching identical
``(model, payload)`` pairs for a configurable TTL.  The cache is keyed on
a deterministic SHA-256 hash of the model identifier and the sorted-keys
JSON serialisation of the payload, so the same logical request always maps
to the same key regardless of dict insertion order.

Design decisions:
    - **Opt-in / zero default overhead.** :class:`ResultCache` is disabled
      by default in :class:`~replicate_mcp.agents.execution.AgentExecutor`.
      Enable it by passing ``cache=ResultCache(ttl_s=300)`` at construction.
      Production workloads should not cache stale responses.
    - **In-memory LRU with configurable TTL.** Uses :class:`collections.OrderedDict`
      to implement LRU eviction without external dependencies.  The cache is
      *not* process-safe across multiple workers — use the optional disk-backed
      mode for shared caching.
    - **Streaming-compatible.** Cached entries store the complete chunk list.
      On a cache hit the chunks are replayed instantly, preserving the same
      interface as a live streamed response.
    - **Privacy-safe key.** Only the SHA-256 hash of the payload is stored as
      the key; the actual payload is **not** retained beyond what the caller
      already holds.
    - **Configurable eviction policies.** Supports LRU (default), TTL-only,
      and FIFO eviction strategies for different workload patterns.

Usage::

    from replicate_mcp.cache import ResultCache, EvictionPolicy
    from replicate_mcp.agents.execution import AgentExecutor

    cache = ResultCache(ttl_s=300, max_entries=500, policy=EvictionPolicy.LRU)
    executor = AgentExecutor(cache=cache)

    async for chunk in executor.run("llama3_chat", {"prompt": "hi"}):
        ...  # first call — live API request

    async for chunk in executor.run("llama3_chat", {"prompt": "hi"}):
        ...  # second call within 5 min — instant cache replay
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock, Thread
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Eviction Policies
# ---------------------------------------------------------------------------


class EvictionPolicy(Enum):
    """Cache eviction policy controlling how entries are removed.

    - ``LRU`` (default): Least Recently Used — entries accessed longest ago
      are evicted first. Best for workloads with temporal locality.
    - ``TTL``: Time-To-Live only — only evict expired entries, never evict
      live entries. Best when all cached content has similar value and
      freshness is the primary concern. May exceed ``max_entries`` until
      background cleanup runs.
    - ``FIFO``: First In, First Out — oldest entries (by insertion time) are
      evicted first regardless of access patterns. Best for streaming
      workloads where newer entries are always more valuable.
    - ``LFU`` (reserved): Least Frequently Used — entries accessed fewest
      times are evicted first. Best for workloads with stable access
      distributions (not yet implemented).
    """

    LRU = "lru"
    TTL = "ttl"
    FIFO = "fifo"
    LFU = "lfu"  # Reserved for future implementation


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Internal cache entry holding chunks and an expiry timestamp."""

    chunks: list[dict[str, Any]]
    expires_at: float  # monotonic clock
    inserted_at: float = field(default_factory=time.monotonic)  # For FIFO
    access_count: int = field(default=0)  # For LFU
    last_accessed: float = field(default_factory=time.monotonic)  # For LRU


# ---------------------------------------------------------------------------
# ResultCache
# ---------------------------------------------------------------------------


class ResultCache:
    """Content-addressed cache for agent invocation results.

    Args:
        ttl_s:       Seconds before a cache entry expires (default 300 = 5 min).
        max_entries: Maximum number of entries to keep in memory.  Oldest
                     (least recently used) entries are evicted first.
                     Default: 500.
        policy:      Eviction policy controlling how entries are removed
                     when capacity is reached. Default: ``LRU``.
        background_eviction: If True, start a background thread that
                     periodically cleans up expired entries. Default: False.
        background_interval_s: Seconds between background eviction runs.
                     Default: 60 seconds.

    The key is derived as::

        SHA-256( f"{model_id}:{json.dumps(payload, sort_keys=True)}" )

    Example::

        cache = ResultCache(ttl_s=60)
        key   = cache.make_key("meta/llama-3", {"prompt": "hi"})
        entry = cache.get(key)          # None on first call
        cache.put(key, chunks)          # store after live call
        entry = cache.get(key)          # [chunk, chunk, ...] on second call
    """

    def __init__(
        self,
        *,
        ttl_s: float = 300.0,
        max_entries: int = 500,
        policy: EvictionPolicy = EvictionPolicy.LRU,
        background_eviction: bool = False,
        background_interval_s: float = 60.0,
    ) -> None:
        # Initialize thread-related attributes first for __del__ safety
        self._lock = Lock()
        self._bg_thread: Thread | None = None
        self._stop_bg = False

        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive, got {ttl_s}")
        if max_entries < 1:
            raise ValueError(f"max_entries must be ≥ 1, got {max_entries}")
        if background_interval_s <= 0:
            raise ValueError(f"background_interval_s must be positive, got {background_interval_s}")
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._policy = policy
        self._background_eviction = background_eviction
        self._background_interval_s = background_interval_s
        # OrderedDict used as an LRU cache (move_to_end on access) or FIFO
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0  # Track total evictions

        if background_eviction:
            self._start_background_eviction()

    def _start_background_eviction(self) -> None:
        """Start background thread for periodic eviction of expired entries."""
        self._bg_thread = Thread(target=self._background_eviction_loop, daemon=True)
        self._bg_thread.start()
        logger.debug("Started background eviction thread (interval=%.1fs)", self._background_interval_s)

    def _background_eviction_loop(self) -> None:
        """Background loop that periodically evicts expired entries."""
        while not self._stop_bg:
            try:
                time.sleep(self._background_interval_s)
                if not self._stop_bg:
                    evicted = self.evict_expired()
                    if evicted > 0:
                        logger.debug("Background eviction removed %d expired entries", evicted)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Background eviction error: %s", exc)

    def stop_background_eviction(self) -> None:
        """Stop the background eviction thread if running."""
        self._stop_bg = True
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=2.0)
            logger.debug("Stopped background eviction thread")

    def __del__(self) -> None:
        """Cleanup background thread on garbage collection."""
        self.stop_background_eviction()

    # ---- public API ----

    @staticmethod
    def make_key(model_id: str, payload: dict[str, Any]) -> str:
        """Derive a deterministic cache key from *model_id* and *payload*.

        The key is the first 32 hex chars of SHA-256 over the concatenation
        ``"{model_id}:{sorted-JSON(payload)}"``.  This is collision-resistant
        for all practical purposes (2¹²⁸ space) and compact enough for dict
        keys.

        Args:
            model_id: Full Replicate model identifier (``owner/name``).
            payload:  Input payload dict.

        Returns:
            32-character lowercase hex string.
        """
        try:
            payload_str = json.dumps(payload, sort_keys=True, default=str)
        except (TypeError, ValueError):
            # Non-serialisable payload — use repr as a fallback key component
            payload_str = repr(sorted(payload.items()) if isinstance(payload, dict) else payload)
        raw = f"{model_id}:{payload_str}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, key: str) -> list[dict[str, Any]] | None:
        """Return cached chunks for *key*, or ``None`` on miss / expiry.

        Updates access tracking based on the configured eviction policy.

        Args:
            key: Cache key produced by :meth:`make_key`.

        Returns:
            A **copy** of the cached chunk list, or ``None``.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None

            if time.monotonic() > entry.expires_at:
                # Expired — evict silently
                del self._store[key]
                self._misses += 1
                logger.debug("Cache entry expired for key %s…", key[:8])
                return None

            # Update access tracking based on policy
            entry.last_accessed = time.monotonic()
            entry.access_count += 1

            # LRU: move to end (most recently used)
            if self._policy == EvictionPolicy.LRU:
                self._store.move_to_end(key)

            self._hits += 1
            logger.debug("Cache HIT for key %s… (%d chunks)", key[:8], len(entry.chunks))
            return list(entry.chunks)  # return a copy to prevent mutation

    def _evict_expired_unlocked(self) -> int:
        """Remove all expired entries without lock (caller must hold lock)."""
        now = time.monotonic()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]
        if expired:
            logger.debug("Cache evicted %d expired entries", len(expired))
        return len(expired)

    def _evict_if_needed(self) -> None:
        """Evict entries if at capacity according to the configured policy."""
        while len(self._store) >= self._max_entries:
            if self._policy == EvictionPolicy.TTL:
                # TTL policy: only evict expired entries
                evicted = self._evict_expired_unlocked()
                if evicted == 0:
                    # No expired entries, can't evict
                    logger.debug("TTL policy: cache at capacity but no expired entries to evict")
                    return
                self._evictions += evicted
                continue

            # Select entry to evict based on policy
            evicted_key: str | None = None

            if self._policy == EvictionPolicy.LRU:
                # LRU: evict first (least recently accessed) entry
                evicted_key, _ = self._store.popitem(last=False)
            elif self._policy == EvictionPolicy.FIFO:
                # FIFO: evict oldest entry by insertion time
                oldest_key = min(
                    self._store.keys(),
                    key=lambda k: self._store[k].inserted_at,
                )
                evicted_key = oldest_key
                del self._store[oldest_key]
            elif self._policy == EvictionPolicy.LFU:
                # LFU: evict least frequently accessed entry
                # For now, fall back to FIFO (LFU is reserved for future implementation)
                oldest_key = min(
                    self._store.keys(),
                    key=lambda k: self._store[k].inserted_at,
                )
                evicted_key = oldest_key
                del self._store[oldest_key]

            if evicted_key:
                self._evictions += 1
                logger.debug("Cache %s evict key %s…", self._policy.value, evicted_key[:8])

    def put(self, key: str, chunks: list[dict[str, Any]]) -> None:
        """Store *chunks* under *key* with a TTL of :attr:`ttl_s` seconds.

        If the cache is at capacity, entries are evicted according to the
        configured :attr:`policy`.  If *key* already exists its entry is refreshed.

        Args:
            key:    Cache key produced by :meth:`make_key`.
            chunks: Complete list of output chunks to cache.
        """
        with self._lock:
            now = time.monotonic()
            if key in self._store:
                # Refresh existing entry
                if self._policy == EvictionPolicy.LRU:
                    self._store.move_to_end(key)
                self._store[key].expires_at = now + self._ttl_s
                self._store[key].chunks = list(chunks)
                self._store[key].last_accessed = now
                return

            # Evict entries if at capacity according to policy
            self._evict_if_needed()

            self._store[key] = _CacheEntry(
                chunks=list(chunks),
                expires_at=now + self._ttl_s,
            )
            logger.debug("Cache PUT key %s… (%d chunks)", key[:8], len(chunks))

    def invalidate(self, key: str) -> bool:
        """Remove *key* from the cache if present.

        Returns:
            ``True`` if the key was present and removed, ``False`` otherwise.
        """
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._store.clear()
            logger.debug("Cache cleared")

    def evict_expired(self) -> int:
        """Remove all expired entries and return the count removed."""
        with self._lock:
            now = time.monotonic()
            expired = [k for k, v in self._store.items() if now > v.expires_at]
            for k in expired:
                del self._store[k]
            if expired:
                logger.debug("Cache evicted %d expired entries", len(expired))
            return len(expired)

    # ---- introspection ----

    @property
    def size(self) -> int:
        """Number of entries currently in the cache (including expired)."""
        return len(self._store)

    @property
    def hits(self) -> int:
        """Cumulative number of cache hits since this instance was created."""
        return self._hits

    @property
    def misses(self) -> int:
        """Cumulative number of cache misses since this instance was created."""
        return self._misses

    @property
    def hit_rate(self) -> float:
        """Hit rate as a fraction in [0, 1]; 0.0 if no lookups yet."""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def ttl_s(self) -> float:
        """Configured TTL in seconds."""
        return self._ttl_s

    @property
    def max_entries(self) -> int:
        """Configured maximum number of entries."""
        return self._max_entries

    @property
    def policy(self) -> EvictionPolicy:
        """Configured eviction policy."""
        return self._policy

    @property
    def evictions(self) -> int:
        """Cumulative number of entries evicted since this instance was created."""
        return self._evictions

    @property
    def stats(self) -> dict[str, Any]:
        """Complete cache statistics as a dictionary.

        Returns:
            Dictionary with size, capacity, hits, misses, hit_rate,
            evictions, policy, and ttl_s.
        """
        return {
            "size": self.size,
            "capacity": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate,
            "evictions": self._evictions,
            "policy": self._policy.value,
            "ttl_s": self._ttl_s,
        }

    def __repr__(self) -> str:
        return (
            f"ResultCache(size={self.size}/{self._max_entries}, "
            f"policy={self._policy.value}, "
            f"ttl_s={self._ttl_s}, "
            f"hit_rate={self.hit_rate:.1%})"
        )


__all__ = ["ResultCache", "EvictionPolicy"]
