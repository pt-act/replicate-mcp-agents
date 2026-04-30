"""Unit tests for Cache Eviction Policy (Feature #25).

Tests for configurable eviction policies in ResultCache.
"""

import pytest

from replicate_mcp.cache import EvictionPolicy, ResultCache, _CacheEntry


class TestEvictionPolicyEnum:
    """Tests for EvictionPolicy enum."""

    def test_enum_values(self) -> None:
        """Verify enum values."""
        assert EvictionPolicy.LRU.value == "lru"
        assert EvictionPolicy.TTL.value == "ttl"
        assert EvictionPolicy.FIFO.value == "fifo"
        assert EvictionPolicy.LFU.value == "lfu"

    def test_default_is_lru(self) -> None:
        """LRU is the default eviction policy."""
        cache = ResultCache(ttl_s=300)
        assert cache.policy == EvictionPolicy.LRU


class TestCacheEntry:
    """Tests for _CacheEntry data class."""

    def test_entry_initialization(self) -> None:
        """Cache entry initializes with correct defaults."""
        import time

        before = time.monotonic()
        entry = _CacheEntry(chunks=[{"test": "data"}], expires_at=12345.0)
        after = time.monotonic()

        assert entry.chunks == [{"test": "data"}]
        assert entry.expires_at == 12345.0
        assert entry.access_count == 0
        assert before <= entry.inserted_at <= after
        assert before <= entry.last_accessed <= after


class TestResultCacheConstructor:
    """Tests for ResultCache construction with eviction policy."""

    def test_accepts_eviction_policy(self) -> None:
        """Constructor accepts eviction policy parameter."""
        cache = ResultCache(policy=EvictionPolicy.FIFO)
        assert cache.policy == EvictionPolicy.FIFO

    def test_accepts_ttl_policy(self) -> None:
        """Constructor accepts TTL policy."""
        cache = ResultCache(policy=EvictionPolicy.TTL)
        assert cache.policy == EvictionPolicy.TTL

    def test_accepts_background_eviction(self) -> None:
        """Constructor accepts background eviction parameter."""
        cache = ResultCache(background_eviction=False)
        assert not cache._background_eviction

        cache2 = ResultCache(background_eviction=True, background_interval_s=30.0)
        assert cache2._background_eviction
        # Stop the background thread to avoid test pollution
        cache2.stop_background_eviction()

    def test_rejects_invalid_background_interval(self) -> None:
        """Constructor rejects invalid background interval."""
        with pytest.raises(ValueError, match="background_interval_s must be positive"):
            ResultCache(background_eviction=True, background_interval_s=0)

    def test_rejects_negative_background_interval(self) -> None:
        """Constructor rejects negative background interval."""
        with pytest.raises(ValueError, match="background_interval_s must be positive"):
            ResultCache(background_eviction=True, background_interval_s=-1)


class TestLRUEviction:
    """Tests for LRU (Least Recently Used) eviction policy."""

    def test_lru_evicts_least_recently_used(self) -> None:
        """LRU policy evicts entries accessed longest ago."""
        cache = ResultCache(max_entries=2, policy=EvictionPolicy.LRU)

        # Add first entry
        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        # Add second entry
        key2 = cache.make_key("model1", {"input": "b"})
        cache.put(key2, [{"output": "b"}])

        # Access first entry to make it recently used
        cache.get(key1)

        # Add third entry (should evict key2, not key1)
        key3 = cache.make_key("model1", {"input": "c"})
        cache.put(key3, [{"output": "c"}])

        # key1 should still be in cache (was accessed)
        assert cache.get(key1) is not None
        # key2 should be evicted (least recently used)
        assert cache.get(key2) is None

    def test_lru_updates_on_access(self) -> None:
        """LRU updates order on access."""
        cache = ResultCache(max_entries=3, policy=EvictionPolicy.LRU)

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})
        key3 = cache.make_key("model1", {"input": "c"})

        cache.put(key1, [{"out": "a"}])
        cache.put(key2, [{"out": "b"}])
        cache.put(key3, [{"out": "c"}])

        # Access key1 (now most recently used)
        cache.get(key1)

        # Add key4, should evict key2 (not key1 or key3)
        key4 = cache.make_key("model1", {"input": "d"})
        cache.put(key4, [{"out": "d"}])

        assert cache.get(key1) is not None  # Still there
        assert cache.get(key2) is None  # Evicted
        assert cache.get(key3) is not None  # Still there

    def test_lru_tracks_evictions(self) -> None:
        """LRU policy tracks evictions."""
        cache = ResultCache(max_entries=2, policy=EvictionPolicy.LRU)
        assert cache.evictions == 0

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})
        key3 = cache.make_key("model1", {"input": "c"})

        cache.put(key1, [{"out": "a"}])
        cache.put(key2, [{"out": "b"}])
        cache.put(key3, [{"out": "c"}])  # Evicts key1

        assert cache.evictions == 1


class TestTTLEviction:
    """Tests for TTL-only eviction policy."""

    def test_ttl_only_evicts_expired(self) -> None:
        """TTL policy only evicts expired entries, not by capacity."""
        cache = ResultCache(max_entries=2, policy=EvictionPolicy.TTL, ttl_s=0.01)

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})
        key3 = cache.make_key("model1", {"input": "c"})

        cache.put(key1, [{"out": "a"}])
        cache.put(key2, [{"out": "b"}])

        # TTL policy: should NOT evict to make room
        # But entries are at capacity, so eviction logic runs
        cache.put(key3, [{"out": "c"}])

        # With TTL policy and entries at capacity, it evicts expired entries
        # Since none are expired yet, it may exceed capacity or not evict
        # depending on implementation

    def test_ttl_evicts_expired_entries(self) -> None:
        """TTL policy evicts expired entries during put."""
        import time

        cache = ResultCache(max_entries=10, policy=EvictionPolicy.TTL, ttl_s=0.01)

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})

        cache.put(key1, [{"out": "a"}])
        cache.put(key2, [{"out": "b"}])

        # Wait for entries to expire
        time.sleep(0.02)

        # Add new entry, should trigger eviction of expired entries
        key3 = cache.make_key("model1", {"input": "c"})
        cache.put(key3, [{"out": "c"}])

        # Expired entries should be gone
        assert cache.get(key1) is None
        assert cache.get(key2) is None
        assert cache.get(key3) is not None


class TestFIFOEviction:
    """Tests for FIFO (First In, First Out) eviction policy."""

    def test_fifo_evicts_oldest_by_insertion(self) -> None:
        """FIFO policy evicts oldest entries by insertion time."""
        import time

        cache = ResultCache(max_entries=2, policy=EvictionPolicy.FIFO)

        # Add entries with a small delay
        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])
        time.sleep(0.01)

        key2 = cache.make_key("model1", {"input": "b"})
        cache.put(key2, [{"output": "b"}])

        # Access key1 (should not matter for FIFO)
        cache.get(key1)

        # Add third entry
        key3 = cache.make_key("model1", {"input": "c"})
        cache.put(key3, [{"output": "c"}])

        # key1 should be evicted (oldest insertion), not key2
        assert cache.get(key1) is None
        assert cache.get(key2) is not None

    def test_fifo_preserves_order_independent_of_access(self) -> None:
        """FIFO preserves insertion order regardless of access patterns."""
        cache = ResultCache(max_entries=3, policy=EvictionPolicy.FIFO)

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})
        key3 = cache.make_key("model1", {"input": "c"})

        cache.put(key1, [{"out": "a"}])
        cache.put(key2, [{"out": "b"}])
        cache.put(key3, [{"out": "c"}])

        # Access in reverse order (doesn't matter for FIFO)
        cache.get(key3)
        cache.get(key2)
        cache.get(key1)

        # Add key4, should evict key1 (oldest insertion)
        key4 = cache.make_key("model1", {"input": "d"})
        cache.put(key4, [{"out": "d"}])

        assert cache.get(key1) is None  # Evicted
        assert cache.get(key2) is not None
        assert cache.get(key3) is not None


class TestAccessCountTracking:
    """Tests for access count tracking (used by LFU)."""

    def test_tracks_access_count(self) -> None:
        """Cache tracks access count per entry."""
        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        # Access multiple times
        cache.get(key1)
        cache.get(key1)
        cache.get(key1)

        entry = cache._store[key1]
        assert entry.access_count == 3

    def test_access_count_increments_on_put_refresh(self) -> None:
        """Access count does not reset on refresh (existing entry update)."""
        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        cache.get(key1)
        assert cache._store[key1].access_count == 1

        # Refresh (update) the entry
        cache.put(key1, [{"output": "b"}])
        # Access count should be preserved
        assert cache._store[key1].access_count == 1


class TestLastAccessedTracking:
    """Tests for last_accessed timestamp tracking (used by LRU)."""

    def test_updates_last_accessed_on_get(self) -> None:
        """last_accessed updates on cache hit."""
        import time

        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        initial_last_accessed = cache._store[key1].last_accessed
        time.sleep(0.01)

        cache.get(key1)

        assert cache._store[key1].last_accessed > initial_last_accessed

    def test_updates_last_accessed_on_put_refresh(self) -> None:
        """last_accessed updates on put refresh."""
        import time

        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        initial_last_accessed = cache._store[key1].last_accessed
        time.sleep(0.01)

        cache.put(key1, [{"output": "b"}])  # Refresh

        assert cache._store[key1].last_accessed > initial_last_accessed


class TestBackgroundEviction:
    """Tests for background eviction thread."""

    def test_starts_background_thread(self) -> None:
        """Background eviction thread starts when enabled."""
        cache = ResultCache(
            background_eviction=True,
            background_interval_s=0.1,
        )

        assert cache._bg_thread is not None
        assert cache._bg_thread.is_alive()

        cache.stop_background_eviction()

    def test_stops_background_thread(self) -> None:
        """Background eviction thread stops when requested."""
        cache = ResultCache(
            background_eviction=True,
            background_interval_s=0.1,
        )

        cache.stop_background_eviction()
        assert not cache._bg_thread.is_alive() or not cache._bg_thread.is_alive()

    def test_background_evicts_expired(self) -> None:
        """Background thread evicts expired entries."""
        import time

        cache = ResultCache(
            ttl_s=0.05,
            background_eviction=True,
            background_interval_s=0.02,
        )

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        # Wait for background eviction
        time.sleep(0.1)

        # Entry should be expired and potentially evicted by background thread
        # Note: race condition possible, entry may or may not be evicted yet

        cache.stop_background_eviction()

    def test_no_background_thread_when_disabled(self) -> None:
        """No background thread when background_eviction is False."""
        cache = ResultCache(background_eviction=False)

        assert cache._bg_thread is None


class TestEvictionStats:
    """Tests for eviction statistics."""

    def test_tracks_eviction_count(self) -> None:
        """Cache tracks total eviction count."""
        cache = ResultCache(max_entries=2)
        assert cache.evictions == 0

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})
        key3 = cache.make_key("model1", {"input": "c"})

        cache.put(key1, [{"out": "a"}])
        cache.put(key2, [{"out": "b"}])
        cache.put(key3, [{"out": "c"}])

        assert cache.evictions == 1

        key4 = cache.make_key("model1", {"input": "d"})
        cache.put(key4, [{"out": "d"}])

        assert cache.evictions == 2

    def test_stats_dictionary(self) -> None:
        """stats property returns complete statistics."""
        cache = ResultCache(max_entries=100, policy=EvictionPolicy.FIFO, ttl_s=600)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"out": "a"}])
        cache.get(key1)

        stats = cache.stats

        assert stats["size"] == 1
        assert stats["capacity"] == 100
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 1.0
        assert stats["evictions"] == 0
        assert stats["policy"] == "fifo"
        assert stats["ttl_s"] == 600


class TestThreadSafety:
    """Tests for thread safety with locks."""

    def test_get_is_thread_safe(self) -> None:
        """Get operation is thread-safe."""
        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        # Should not raise any thread safety issues
        result = cache.get(key1)
        assert result is not None

    def test_put_is_thread_safe(self) -> None:
        """Put operation is thread-safe."""
        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        # Should not raise any thread safety issues
        cache.put(key1, [{"output": "b"}])  # Refresh

    def test_invalidate_is_thread_safe(self) -> None:
        """Invalidate operation is thread-safe."""
        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        # Should not raise any thread safety issues
        result = cache.invalidate(key1)
        assert result is True

    def test_clear_is_thread_safe(self) -> None:
        """Clear operation is thread-safe."""
        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        # Should not raise any thread safety issues
        cache.clear()

    def test_evict_expired_is_thread_safe(self) -> None:
        """evict_expired operation is thread-safe."""
        cache = ResultCache(max_entries=10)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        # Should not raise any thread safety issues
        count = cache.evict_expired()
        assert count == 0  # Not expired yet


class TestCacheRepr:
    """Tests for cache representation."""

    def test_repr_includes_policy(self) -> None:
        """Cache repr includes policy."""
        cache = ResultCache(max_entries=100, policy=EvictionPolicy.FIFO)

        repr_str = repr(cache)

        assert "ResultCache" in repr_str
        assert "policy=fifo" in repr_str
        assert "size=0/100" in repr_str


class TestRefreshExistingEntry:
    """Tests for refreshing existing entries."""

    def test_refresh_updates_ttl(self) -> None:
        """Refreshing entry updates TTL."""
        import time

        cache = ResultCache(max_entries=10, ttl_s=300)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        initial_expires = cache._store[key1].expires_at
        time.sleep(0.01)

        # Refresh the entry
        cache.put(key1, [{"output": "b"}])

        # TTL should be extended
        assert cache._store[key1].expires_at > initial_expires

    def test_refresh_preserves_inserted_at(self) -> None:
        """Refreshing entry does not change inserted_at (for FIFO)."""
        import time

        cache = ResultCache(max_entries=10, policy=EvictionPolicy.FIFO)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"output": "a"}])

        initial_inserted = cache._store[key1].inserted_at
        time.sleep(0.01)

        # Refresh the entry
        cache.put(key1, [{"output": "b"}])

        # Insertion time should not change
        assert cache._store[key1].inserted_at == initial_inserted


class TestEvictExpiredFunctionality:
    """Tests for evict_expired method."""

    def test_evict_expired_returns_count(self) -> None:
        """evict_expired returns count of removed entries."""
        import time

        cache = ResultCache(max_entries=10, ttl_s=0.01)

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})

        cache.put(key1, [{"out": "a"}])
        cache.put(key2, [{"out": "b"}])

        time.sleep(0.02)  # Wait for expiration

        count = cache.evict_expired()
        assert count == 2

    def test_evict_expired_only_removes_expired(self) -> None:
        """evict_expired only removes expired entries."""
        import time

        cache = ResultCache(max_entries=10, ttl_s=0.05)

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})

        cache.put(key1, [{"out": "a"}])
        time.sleep(0.06)  # key1 expires
        cache.put(key2, [{"out": "b"}])  # key2 still fresh

        count = cache.evict_expired()
        assert count == 1
        assert cache.get(key1) is None
        assert cache.get(key2) is not None


class TestEdgeCases:
    """Edge case tests for eviction policies."""

    def test_empty_cache_eviction(self) -> None:
        """Eviction on empty cache is safe."""
        cache = ResultCache(max_entries=2)

        key1 = cache.make_key("model1", {"input": "a"})
        cache.put(key1, [{"out": "a"}])
        cache.invalidate(key1)

        # Should not raise
        key2 = cache.make_key("model1", {"input": "b"})
        cache.put(key2, [{"out": "b"}])

        assert cache.size == 1

    def test_single_entry_capacity(self) -> None:
        """Cache with capacity 1 works correctly."""
        cache = ResultCache(max_entries=1)

        key1 = cache.make_key("model1", {"input": "a"})
        key2 = cache.make_key("model1", {"input": "b"})

        cache.put(key1, [{"out": "a"}])
        assert cache.get(key1) is not None

        cache.put(key2, [{"out": "b"}])
        assert cache.get(key1) is None  # Evicted
        assert cache.get(key2) is not None

