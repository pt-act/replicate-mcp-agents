"""Tests for replicate_mcp.discovery — ModelDiscovery and helpers."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from replicate_mcp.agents.registry import AgentRegistry
from replicate_mcp.discovery import (
    DiscoveryConfig,
    DiscoveryResult,
    ModelDiscovery,
    _model_to_metadata,
    discover_and_register,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(owner: str = "meta", name: str = "llama", **kwargs: Any) -> Any:
    tags = kwargs.pop("tags", [])
    return SimpleNamespace(owner=owner, name=name, description="A model", tags=tags, **kwargs)


def _install_fake_replicate(models: list[Any]) -> None:
    fake = ModuleType("replicate")
    mock_client_cls = MagicMock()
    mock_client = MagicMock()
    mock_client.models.list.return_value = iter(models)
    mock_client_cls.return_value = mock_client
    fake.Client = mock_client_cls  # type: ignore[attr-defined]
    import sys
    sys.modules["replicate"] = fake


def _remove_fake_replicate() -> None:
    import sys
    sys.modules.pop("replicate", None)


# ---------------------------------------------------------------------------
# DiscoveryConfig
# ---------------------------------------------------------------------------


class TestDiscoveryConfig:
    def test_defaults(self) -> None:
        cfg = DiscoveryConfig()
        assert cfg.owner is None
        assert cfg.required_tags == []
        assert cfg.max_models == 50
        assert cfg.ttl_seconds == 300.0
        assert cfg.auto_streaming is True

    def test_custom(self) -> None:
        cfg = DiscoveryConfig(owner="meta", max_models=10, ttl_seconds=60.0)
        assert cfg.owner == "meta"
        assert cfg.max_models == 10
        assert cfg.ttl_seconds == 60.0


# ---------------------------------------------------------------------------
# DiscoveryResult
# ---------------------------------------------------------------------------


class TestDiscoveryResult:
    def test_total_registered(self) -> None:
        r = DiscoveryResult(registered=3, updated=2)
        assert r.total_registered == 5

    def test_defaults(self) -> None:
        r = DiscoveryResult()
        assert r.discovered == 0
        assert r.errors == []


# ---------------------------------------------------------------------------
# _model_to_metadata
# ---------------------------------------------------------------------------


class TestModelToMetadata:
    def test_basic_conversion(self) -> None:
        cfg = DiscoveryConfig()
        model = _make_model()
        meta = _model_to_metadata(model, cfg)
        assert meta is not None
        assert "meta" in meta.safe_name
        assert "llama" in meta.safe_name
        assert meta.model == "meta/llama"
        assert "auto-discovered" in meta.tags

    def test_owner_filter_passes(self) -> None:
        cfg = DiscoveryConfig(owner="meta")
        meta = _model_to_metadata(_make_model(owner="meta"), cfg)
        assert meta is not None

    def test_owner_filter_fails(self) -> None:
        cfg = DiscoveryConfig(owner="mistral")
        meta = _model_to_metadata(_make_model(owner="meta"), cfg)
        assert meta is None

    def test_tag_filter_passes(self) -> None:
        cfg = DiscoveryConfig(required_tags=["chat"])
        model = _make_model(tags=["chat", "llm"])
        meta = _model_to_metadata(model, cfg)
        assert meta is not None

    def test_tag_filter_fails(self) -> None:
        cfg = DiscoveryConfig(required_tags=["vision"])
        model = _make_model(tags=["chat"])
        meta = _model_to_metadata(model, cfg)
        assert meta is None

    def test_missing_owner_returns_none(self) -> None:
        cfg = DiscoveryConfig()
        model = SimpleNamespace(owner="", name="llama", description="x", tags=[])
        meta = _model_to_metadata(model, cfg)
        assert meta is None

    def test_missing_name_returns_none(self) -> None:
        cfg = DiscoveryConfig()
        model = SimpleNamespace(owner="meta", name=None, description="x", tags=[])
        meta = _model_to_metadata(model, cfg)
        assert meta is None

    def test_hyphen_sanitized(self) -> None:
        cfg = DiscoveryConfig()
        model = _make_model(owner="some-owner", name="my-model")
        meta = _model_to_metadata(model, cfg)
        assert meta is not None
        assert "-" not in meta.safe_name

    def test_exception_returns_none(self) -> None:
        cfg = DiscoveryConfig()
        meta = _model_to_metadata(None, cfg)
        assert meta is None

    def test_auto_streaming_flag(self) -> None:
        cfg = DiscoveryConfig(auto_streaming=False)
        meta = _model_to_metadata(_make_model(), cfg)
        assert meta is not None
        assert meta.supports_streaming is False


# ---------------------------------------------------------------------------
# ModelDiscovery.is_fresh / _is_fresh
# ---------------------------------------------------------------------------


class TestModelDiscoveryFreshness:
    def test_not_fresh_on_init(self) -> None:
        disc = ModelDiscovery(AgentRegistry())
        assert not disc.is_fresh()
        assert disc.last_result is None

    def test_fresh_after_refresh(self) -> None:
        # Patch _fetch_and_register to avoid real API call
        disc = ModelDiscovery(AgentRegistry(), DiscoveryConfig(ttl_seconds=300))

        async def _fake_fetch(*, api_token: str | None) -> DiscoveryResult:
            return DiscoveryResult(discovered=1, registered=1)

        disc._fetch_and_register = _fake_fetch  # type: ignore[method-assign]

        async def run() -> None:
            await disc.refresh()
            assert disc.is_fresh()
            assert disc.last_result is not None
            assert disc.last_result.discovered == 1

        asyncio.get_event_loop().run_until_complete(run())

    def test_cached_result_returned_on_second_call(self) -> None:
        disc = ModelDiscovery(AgentRegistry(), DiscoveryConfig(ttl_seconds=300))
        call_count = 0

        async def _fake_fetch(*, api_token: str | None) -> DiscoveryResult:
            nonlocal call_count
            call_count += 1
            return DiscoveryResult(discovered=call_count)

        disc._fetch_and_register = _fake_fetch  # type: ignore[method-assign]

        async def run() -> None:
            r1 = await disc.refresh()
            r2 = await disc.refresh()  # should use cache
            assert call_count == 1
            assert r1.discovered == r2.discovered

        asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# ModelDiscovery.refresh with fake replicate
# ---------------------------------------------------------------------------


class TestModelDiscoveryRefresh:
    def setup_method(self) -> None:
        _remove_fake_replicate()

    def teardown_method(self) -> None:
        _remove_fake_replicate()

    def test_refresh_registers_models(self) -> None:
        models = [_make_model("meta", "llama"), _make_model("mistral", "mixtral")]
        _install_fake_replicate(models)

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))

        async def run() -> None:
            result = await disc.refresh()
            assert result.registered == 2
            assert result.discovered == 2
            assert result.errors == []

        asyncio.get_event_loop().run_until_complete(run())

    def test_refresh_respects_max_models(self) -> None:
        models = [_make_model("m", f"model{i}") for i in range(10)]
        _install_fake_replicate(models)

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(max_models=3, ttl_seconds=0))

        async def run() -> None:
            result = await disc.refresh()
            assert result.discovered == 3

        asyncio.get_event_loop().run_until_complete(run())

    def test_refresh_updates_existing(self) -> None:
        models = [_make_model("meta", "llama")]
        _install_fake_replicate(models)

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))

        async def run() -> None:
            await disc.refresh()
            _install_fake_replicate(models)
            disc._last_refresh = 0.0  # force refresh
            disc._last_result = None
            result = await disc.refresh()
            assert result.updated == 1

        asyncio.get_event_loop().run_until_complete(run())

    def test_refresh_without_replicate_returns_error(self) -> None:
        import sys
        sys.modules.pop("replicate", None)

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))

        async def run() -> None:
            result = await disc.refresh()
            assert len(result.errors) > 0

        asyncio.get_event_loop().run_until_complete(run())

    def test_refresh_api_failure_records_error(self) -> None:
        fake = ModuleType("replicate")
        mock_client_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.models.list.side_effect = RuntimeError("API error")
        mock_client_cls.return_value = mock_client
        fake.Client = mock_client_cls  # type: ignore[attr-defined]
        import sys
        sys.modules["replicate"] = fake

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))

        async def run() -> None:
            result = await disc.refresh()
            assert len(result.errors) > 0

        asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# Background refresh
# ---------------------------------------------------------------------------


class TestBackgroundRefresh:
    def test_start_and_stop(self) -> None:
        disc = ModelDiscovery(AgentRegistry(), DiscoveryConfig(
            ttl_seconds=0,
            background_interval_seconds=0.05,
        ))

        async def _fake_fetch(*, api_token: str | None) -> DiscoveryResult:
            return DiscoveryResult(discovered=1)

        disc._fetch_and_register = _fake_fetch  # type: ignore[method-assign]

        async def run() -> None:
            task = disc.start_background_refresh()
            assert not task.done()
            await asyncio.sleep(0.12)
            disc.stop_background_refresh()
            # Should have refreshed at least once
            assert disc.last_result is not None

        asyncio.get_event_loop().run_until_complete(run())

    def test_start_raises_when_interval_zero(self) -> None:
        disc = ModelDiscovery(AgentRegistry(), DiscoveryConfig(background_interval_seconds=0))
        with pytest.raises(ValueError, match="background_interval_seconds"):
            disc.start_background_refresh()

    def test_start_returns_same_task_if_running(self) -> None:
        disc = ModelDiscovery(AgentRegistry(), DiscoveryConfig(
            ttl_seconds=0,
            background_interval_seconds=0.1,
        ))

        async def _fake_fetch(*, api_token: str | None) -> DiscoveryResult:
            return DiscoveryResult()

        disc._fetch_and_register = _fake_fetch  # type: ignore[method-assign]

        async def run() -> None:
            t1 = disc.start_background_refresh()
            t2 = disc.start_background_refresh()
            assert t1 is t2
            disc.stop_background_refresh()

        asyncio.get_event_loop().run_until_complete(run())

    def test_stop_noop_when_not_running(self) -> None:
        disc = ModelDiscovery(AgentRegistry())
        disc.stop_background_refresh()  # Should not raise


# ---------------------------------------------------------------------------
# discover_and_register
# ---------------------------------------------------------------------------


class TestDiscoverAndRegister:
    def setup_method(self) -> None:
        _remove_fake_replicate()

    def teardown_method(self) -> None:
        _remove_fake_replicate()

    def test_creates_registry_when_none(self) -> None:
        import sys
        sys.modules.pop("replicate", None)

        async def run() -> None:
            reg, result = await discover_and_register()
            assert isinstance(reg, AgentRegistry)
            assert len(result.errors) > 0  # no replicate installed

        asyncio.get_event_loop().run_until_complete(run())

    def test_uses_provided_registry(self) -> None:
        import sys
        sys.modules.pop("replicate", None)

        existing = AgentRegistry()

        async def run() -> None:
            reg, _ = await discover_and_register(registry=existing)
            assert reg is existing

        asyncio.get_event_loop().run_until_complete(run())
