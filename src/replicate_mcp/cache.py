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

Usage::

    from replicate_mcp.cache import ResultCache
    from replicate_mcp.agents.execution import AgentExecutor

    cache = ResultCache(ttl_s=300, max_entries=500)
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
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Internal cache entry holding chunks and an expiry timestamp."""

    chunks: list[dict[str, Any]]
    expires_at: float  # monotonic clock


# ---------------------------------------------------------------------------
# ResultCache
# ---------------------------------------------------------------------------


class ResultCache:
    """Content-addressed LRU cache for agent invocation results.

    Args:
        ttl_s:       Seconds before a cache entry expires (default 300 = 5 min).
        max_entries: Maximum number of entries to keep in memory.  Oldest
                     (least recently used) entries are evicted first.
                     Default: 500.

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
    ) -> None:
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive, got {ttl_s}")
        if max_entries < 1:
            raise ValueError(f"max_entries must be ≥ 1, got {max_entries}")
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        # OrderedDict used as an LRU cache (move_to_end on access)
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

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

        Moves the entry to the most-recently-used position on hit.

        Args:
            key: Cache key produced by :meth:`make_key`.

        Returns:
            A **copy** of the cached chunk list, or ``None``.
        """
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

        # LRU: move to end (most recently used)
        self._store.move_to_end(key)
        self._hits += 1
        logger.debug("Cache HIT for key %s… (%d chunks)", key[:8], len(entry.chunks))
        return list(entry.chunks)  # return a copy to prevent mutation

    def put(self, key: str, chunks: list[dict[str, Any]]) -> None:
        """Store *chunks* under *key* with a TTL of :attr:`ttl_s` seconds.

        If the cache is at capacity, the **least recently used** entry is
        evicted first.  If *key* already exists its entry is refreshed.

        Args:
            key:    Cache key produced by :meth:`make_key`.
            chunks: Complete list of output chunks to cache.
        """
        if key in self._store:
            # Refresh existing entry
            self._store.move_to_end(key)
            self._store[key].expires_at = time.monotonic() + self._ttl_s
            self._store[key].chunks = list(chunks)
            return

        # Evict LRU entry if at capacity
        while len(self._store) >= self._max_entries:
            evicted_key, _ = self._store.popitem(last=False)
            logger.debug("Cache LRU evict key %s…", evicted_key[:8])

        self._store[key] = _CacheEntry(
            chunks=list(chunks),
            expires_at=time.monotonic() + self._ttl_s,
        )
        logger.debug("Cache PUT key %s… (%d chunks)", key[:8], len(chunks))

    def invalidate(self, key: str) -> bool:
        """Remove *key* from the cache if present.

        Returns:
            ``True`` if the key was present and removed, ``False`` otherwise.
        """
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._store.clear()
        logger.debug("Cache cleared")

    def evict_expired(self) -> int:
        """Remove all expired entries and return the count removed."""
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

    def __repr__(self) -> str:
        return (
            f"ResultCache(size={self.size}/{self._max_entries}, "
            f"ttl_s={self._ttl_s}, "
            f"hit_rate={self.hit_rate:.1%})"
        )


__all__ = ["ResultCache"]
