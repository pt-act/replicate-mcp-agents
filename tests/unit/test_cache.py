"""Tests for replicate_mcp.cache — content-addressed result cache."""

from __future__ import annotations

import time
from typing import Any

import pytest

from replicate_mcp.cache import ResultCache


class TestResultCacheInit:
    def test_default_parameters(self) -> None:
        cache = ResultCache()
        assert cache.ttl_s == pytest.approx(300.0)
        assert cache.max_entries == 500
        assert cache.size == 0

    def test_custom_parameters(self) -> None:
        cache = ResultCache(ttl_s=60.0, max_entries=100)
        assert cache.ttl_s == pytest.approx(60.0)
        assert cache.max_entries == 100

    def test_invalid_ttl_raises(self) -> None:
        with pytest.raises(ValueError, match="ttl_s must be positive"):
            ResultCache(ttl_s=0.0)

    def test_invalid_max_entries_raises(self) -> None:
        with pytest.raises(ValueError, match="max_entries must be"):
            ResultCache(max_entries=0)


class TestMakeKey:
    def test_deterministic(self) -> None:
        a = ResultCache.make_key("m/m", {"prompt": "hi"})
        b = ResultCache.make_key("m/m", {"prompt": "hi"})
        assert a == b

    def test_different_model_different_key(self) -> None:
        a = ResultCache.make_key("a/m", {"prompt": "hi"})
        b = ResultCache.make_key("b/m", {"prompt": "hi"})
        assert a != b

    def test_different_payload_different_key(self) -> None:
        a = ResultCache.make_key("m/m", {"prompt": "hi"})
        b = ResultCache.make_key("m/m", {"prompt": "bye"})
        assert a != b

    def test_key_is_32_hex_chars(self) -> None:
        key = ResultCache.make_key("m/m", {})
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_payload_order_independent(self) -> None:
        a = ResultCache.make_key("m/m", {"a": 1, "b": 2})
        b = ResultCache.make_key("m/m", {"b": 2, "a": 1})
        assert a == b

    def test_circular_reference_payload_falls_back_to_repr(self) -> None:
        """Circular reference in payload triggers ValueError → repr fallback."""
        d: dict[str, Any] = {}
        d["self"] = d  # circular reference
        key = ResultCache.make_key("m/m", d)
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)


class TestGetPut:
    def test_miss_on_empty_cache(self) -> None:
        cache = ResultCache()
        assert cache.get("nosuchkey") is None

    def test_put_then_get(self) -> None:
        cache = ResultCache()
        chunks = [{"chunk": "hello"}, {"done": True}]
        cache.put("mykey", chunks)
        result = cache.get("mykey")
        assert result == chunks

    def test_get_returns_copy(self) -> None:
        cache = ResultCache()
        chunks = [{"chunk": "hello"}]
        cache.put("k", chunks)
        result = cache.get("k")
        assert result is not None
        result.append({"chunk": "extra"})
        # Stored chunks should not be mutated
        assert cache.get("k") == chunks

    def test_hit_increments_counter(self) -> None:
        cache = ResultCache()
        cache.put("k", [])
        cache.get("k")
        assert cache.hits == 1
        assert cache.misses == 0

    def test_miss_increments_counter(self) -> None:
        cache = ResultCache()
        cache.get("k")
        assert cache.hits == 0
        assert cache.misses == 1

    def test_hit_rate_with_mixed_access(self) -> None:
        cache = ResultCache()
        cache.put("k", [])
        cache.get("k")  # hit
        cache.get("nope")  # miss
        assert cache.hit_rate == pytest.approx(0.5)

    def test_hit_rate_zero_on_no_access(self) -> None:
        cache = ResultCache()
        assert cache.hit_rate == pytest.approx(0.0)

    def test_expired_entry_is_miss(self) -> None:
        cache = ResultCache(ttl_s=0.05)
        cache.put("k", [{"done": True}])
        time.sleep(0.1)
        assert cache.get("k") is None

    def test_refresh_existing_entry(self) -> None:
        cache = ResultCache()
        cache.put("k", [{"chunk": "v1"}])
        cache.put("k", [{"chunk": "v2"}])
        assert cache.get("k") == [{"chunk": "v2"}]
        assert cache.size == 1

    def test_size_increases_with_entries(self) -> None:
        cache = ResultCache()
        cache.put("k1", [])
        cache.put("k2", [])
        assert cache.size == 2


class TestLRUEviction:
    def test_evicts_lru_when_full(self) -> None:
        cache = ResultCache(ttl_s=300.0, max_entries=3)
        cache.put("a", [{"a": 1}])
        cache.put("b", [{"b": 2}])
        cache.put("c", [{"c": 3}])

        # Access 'a' to make 'b' the LRU
        cache.get("a")

        # Adding a fourth entry should evict 'b' (LRU)
        cache.put("d", [{"d": 4}])

        assert cache.size == 3
        assert cache.get("a") is not None
        assert cache.get("c") is not None
        assert cache.get("d") is not None
        assert cache.get("b") is None  # evicted

    def test_does_not_exceed_max_entries(self) -> None:
        cache = ResultCache(ttl_s=300.0, max_entries=5)
        for i in range(20):
            cache.put(f"key{i}", [])
        assert cache.size == 5


class TestInvalidateAndClear:
    def test_invalidate_removes_entry(self) -> None:
        cache = ResultCache()
        cache.put("k", [])
        removed = cache.invalidate("k")
        assert removed is True
        assert cache.get("k") is None

    def test_invalidate_nonexistent_returns_false(self) -> None:
        cache = ResultCache()
        assert cache.invalidate("nope") is False

    def test_clear_removes_all(self) -> None:
        cache = ResultCache()
        for i in range(5):
            cache.put(f"k{i}", [])
        cache.clear()
        assert cache.size == 0

    def test_evict_expired(self) -> None:
        cache = ResultCache(ttl_s=0.05)
        cache.put("a", [])
        cache.put("b", [])
        time.sleep(0.1)
        cache.put("c", [])  # fresh entry
        evicted = cache.evict_expired()
        assert evicted == 2
        assert cache.size == 1
        assert cache.get("c") is not None


class TestRepr:
    def test_repr_contains_size(self) -> None:
        cache = ResultCache()
        cache.put("k", [])
        r = repr(cache)
        assert "ResultCache" in r
        assert "1/" in r
