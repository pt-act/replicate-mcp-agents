"""Tests for RouterStateManager and CostAwareRouter state persistence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from replicate_mcp.routing import CostAwareRouter, ModelStats
from replicate_mcp.utils.router_state import (
    RouterStateManager,
    deserialise_stats,
    serialise_stats,
)

# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


class TestSerialiseStats:
    def test_roundtrip(self) -> None:
        stats = ModelStats(
            model="owner/model",
            alpha=0.3,
            ema_latency_ms=2000.0,
            ema_cost_usd=0.005,
            ema_quality=0.9,
            invocation_count=42,
            success_count=40,
            ts_alpha=41.0,
            ts_beta=3.0,
        )
        raw = serialise_stats(stats)
        restored = deserialise_stats(raw)
        assert restored.model == stats.model
        assert restored.invocation_count == 42
        assert restored.ts_alpha == pytest.approx(41.0)
        assert restored.ts_beta == pytest.approx(3.0)
        assert restored.ema_cost_usd == pytest.approx(0.005)

    def test_deserialise_ignores_unknown_keys(self) -> None:
        raw = {
            "model": "a/b",
            "ema_latency_ms": 1000.0,
            "ema_cost_usd": 0.01,
            "invocation_count": 0,
            "success_count": 0,
            "ts_alpha": 1.0,
            "ts_beta": 1.0,
            "unknown_future_field": "value",
        }
        stats = deserialise_stats(raw)
        assert stats.model == "a/b"

    def test_serialise_produces_json_safe_dict(self) -> None:
        stats = ModelStats(model="x/y")
        raw = serialise_stats(stats)
        # Must be JSON-serialisable without error
        json.dumps(raw)


# ---------------------------------------------------------------------------
# CostAwareRouter.dump_state / load_state
# ---------------------------------------------------------------------------


class TestRouterDumpLoad:
    def test_dump_empty_router(self) -> None:
        router = CostAwareRouter()
        state = router.dump_state()
        assert state == {}

    def test_dump_load_roundtrip(self) -> None:
        router = CostAwareRouter()
        router.register_model("a/llama", initial_cost=0.002, initial_latency_ms=3000)
        router.record_outcome("a/llama", latency_ms=3100, cost_usd=0.0021, success=True)
        router.record_outcome("a/llama", latency_ms=2900, cost_usd=0.0019, success=False)

        state = router.dump_state()

        new_router = CostAwareRouter()
        new_router.load_state(state)

        restored = new_router.stats()
        assert "a/llama" in restored
        assert restored["a/llama"].invocation_count == 2
        assert restored["a/llama"].success_count == 1
        assert restored["a/llama"].ts_alpha == pytest.approx(2.0)  # 1 + 1 success
        assert restored["a/llama"].ts_beta == pytest.approx(2.0)  # 1 + 1 failure

    def test_load_state_does_not_remove_unmentioned_models(self) -> None:
        router = CostAwareRouter()
        router.register_model("a/model-a")
        router.load_state({"b/model-b": serialise_stats(ModelStats(model="b/model-b"))})

        stats = router.stats()
        assert "a/model-a" in stats  # pre-existing model untouched
        assert "b/model-b" in stats  # newly loaded

    def test_load_state_overwrites_existing_entry(self) -> None:
        router = CostAwareRouter()
        router.register_model("a/m", initial_cost=0.01)
        # Record some outcomes to dirty up the stats
        router.record_outcome("a/m", latency_ms=1000, cost_usd=0.01, success=True)

        clean = serialise_stats(ModelStats(model="a/m"))
        router.load_state({"a/m": clean})

        assert router.stats()["a/m"].invocation_count == 0

    def test_load_state_bad_entry_skipped_gracefully(self) -> None:
        router = CostAwareRouter()
        # Pass a malformed entry
        router.load_state({"bad/model": {"model": "bad/model", "ema_latency_ms": "not-a-float"}})
        # Router should not crash; bad model may or may not be present


# ---------------------------------------------------------------------------
# RouterStateManager — save / load
# ---------------------------------------------------------------------------


class TestRouterStateManager:
    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "router-state.json"
        manager = RouterStateManager(path=path)

        router = CostAwareRouter()
        router.register_model("meta/llama", initial_cost=0.002)
        router.record_outcome("meta/llama", latency_ms=3000, cost_usd=0.002, success=True)

        written_path = manager.save_router(router)
        assert written_path == path
        assert path.exists()

        new_router = CostAwareRouter()
        count = manager.load_into_router(new_router)
        assert count == 1
        assert "meta/llama" in new_router.stats()
        assert new_router.stats()["meta/llama"].invocation_count == 1

    def test_load_no_file_is_noop(self, tmp_path: Path) -> None:
        manager = RouterStateManager(path=tmp_path / "nonexistent.json")
        router = CostAwareRouter()
        count = manager.load_into_router(router)
        assert count == 0
        assert router.stats() == {}

    def test_exists_false_before_save(self, tmp_path: Path) -> None:
        manager = RouterStateManager(path=tmp_path / "state.json")
        assert not manager.exists()

    def test_exists_true_after_save(self, tmp_path: Path) -> None:
        manager = RouterStateManager(path=tmp_path / "state.json")
        router = CostAwareRouter()
        manager.save_router(router)
        assert manager.exists()

    def test_delete_removes_file(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        manager = RouterStateManager(path=path)
        manager.save_router(CostAwareRouter())
        assert path.exists()
        manager.delete()
        assert not path.exists()

    def test_delete_nonexistent_is_noop(self, tmp_path: Path) -> None:
        manager = RouterStateManager(path=tmp_path / "nope.json")
        manager.delete()  # must not raise

    def test_load_malformed_json_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()
        count = manager.load_into_router(router)
        assert count == 0

    def test_load_wrong_schema_version_is_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "old.json"
        path.write_text(json.dumps({"_meta": {"schema_version": 99}, "models": {}}))
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()
        count = manager.load_into_router(router)
        assert count == 0

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "state.json"
        manager = RouterStateManager(path=path)
        manager.save_router(CostAwareRouter())
        assert path.exists()

    def test_multiple_models(self, tmp_path: Path) -> None:
        path = tmp_path / "multi.json"
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()
        for name in ["a/m1", "b/m2", "c/m3"]:
            router.register_model(name)
            router.record_outcome(name, latency_ms=1000, cost_usd=0.001, success=True)

        manager.save_router(router)

        restored = CostAwareRouter()
        count = manager.load_into_router(restored)
        assert count == 3
        for name in ["a/m1", "b/m2", "c/m3"]:
            assert name in restored.stats()


# ---------------------------------------------------------------------------
# auto_save context manager
# ---------------------------------------------------------------------------


class TestAutoSave:
    def test_auto_save_saves_on_exit(self, tmp_path: Path) -> None:
        path = tmp_path / "auto.json"
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()
        router.register_model("x/y")

        async def _run() -> None:
            async with manager.auto_save(router, interval_s=1000.0, save_on_exit=True):
                pass

        asyncio.run(_run())
        assert path.exists()

    def test_auto_save_no_exit_save_if_disabled(self, tmp_path: Path) -> None:
        path = tmp_path / "no-exit.json"
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()

        async def _run() -> None:
            async with manager.auto_save(router, interval_s=1000.0, save_on_exit=False):
                pass

        asyncio.run(_run())
        assert not path.exists()

    def test_auto_save_background_loop(self, tmp_path: Path) -> None:
        """The background loop fires on its interval."""
        path = tmp_path / "loop.json"
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()
        router.register_model("a/b")

        async def _run() -> None:
            async with manager.auto_save(router, interval_s=0.05, save_on_exit=False):
                await asyncio.sleep(0.15)  # let the loop fire at least once

        asyncio.run(_run())
        assert path.exists()

    def test_auto_save_background_exception_is_caught(self, tmp_path: Path) -> None:
        """Exception in background save loop is logged, not raised."""
        from unittest.mock import patch  # noqa: PLC0415

        path = tmp_path / "bg-err.json"
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()

        async def _run() -> None:
            with patch.object(manager, "save_router", side_effect=OSError("write failed")):
                async with manager.auto_save(router, interval_s=0.05, save_on_exit=False):
                    await asyncio.sleep(0.15)  # let the loop fire

        # Should not raise
        asyncio.run(_run())

    def test_auto_save_final_save_exception_is_caught(self, tmp_path: Path) -> None:
        """Exception in final save-on-exit is logged, not raised."""
        from unittest.mock import patch  # noqa: PLC0415

        path = tmp_path / "final-err.json"
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()

        async def _run() -> None:
            with patch.object(manager, "save_router", side_effect=OSError("write failed")):
                async with manager.auto_save(router, interval_s=1000.0, save_on_exit=True):
                    pass

        # Should not raise even though final save fails
        asyncio.run(_run())
        assert not path.exists()  # save never succeeded

    def test_save_atomic_cleanup_on_failure(self, tmp_path: Path) -> None:
        """When save_router fails mid-write, temp file is cleaned up."""
        from unittest.mock import patch  # noqa: PLC0415

        path = tmp_path / "fail.json"
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()

        with patch("json.dump", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                manager.save_router(router)
        # No state file should exist
        assert not path.exists()

    def test_save_unlink_oserror_suppressed(self, tmp_path: Path) -> None:
        """When os.unlink raises OSError during cleanup, it is suppressed."""
        import os
        from unittest.mock import patch  # noqa: PLC0415

        path = tmp_path / "unlink-err.json"
        manager = RouterStateManager(path=path)
        router = CostAwareRouter()

        real_unlink = os.unlink
        call_count = 0

        def _unlink_and_fail(p: str) -> None:
            nonlocal call_count
            call_count += 1
            real_unlink(p)
            raise OSError("permission denied")

        with patch("json.dump", side_effect=RuntimeError("write fail")):
            with patch("os.unlink", side_effect=_unlink_and_fail):
                with pytest.raises(RuntimeError, match="write fail"):
                    manager.save_router(router)
        assert call_count >= 1
