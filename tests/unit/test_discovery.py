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

    def test_exception_during_attribute_access(self) -> None:
        """Model whose attribute access raises — covers lines 156-158."""

        class BadModel:
            @property
            def owner(self) -> str:  # type: ignore[override]
                raise RuntimeError("boom")

        cfg = DiscoveryConfig()
        meta = _model_to_metadata(BadModel(), cfg)
        assert meta is None

    def test_model_with_no_description_uses_default(self) -> None:
        """When description is None, the default string is used."""
        cfg = DiscoveryConfig()
        model = SimpleNamespace(owner="meta", name="llama", description=None, tags=[])
        meta = _model_to_metadata(model, cfg)
        assert meta is not None
        assert "Auto-discovered" in meta.description

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

    @pytest.mark.asyncio
    async def test_fresh_after_refresh(self) -> None:
        # Patch _fetch_and_register to avoid real API call
        disc = ModelDiscovery(AgentRegistry(), DiscoveryConfig(ttl_seconds=300))

        async def _fake_fetch(*, api_token: str | None) -> DiscoveryResult:
            return DiscoveryResult(discovered=1, registered=1)

        disc._fetch_and_register = _fake_fetch  # type: ignore[method-assign]

        await disc.refresh()
        assert disc.is_fresh()
        assert disc.last_result is not None
        assert disc.last_result.discovered == 1

    @pytest.mark.asyncio
    async def test_cached_result_returned_on_second_call(self) -> None:
        disc = ModelDiscovery(AgentRegistry(), DiscoveryConfig(ttl_seconds=300))
        call_count = 0

        async def _fake_fetch(*, api_token: str | None) -> DiscoveryResult:
            nonlocal call_count
            call_count += 1
            return DiscoveryResult(discovered=call_count)

        disc._fetch_and_register = _fake_fetch  # type: ignore[method-assign]

        r1 = await disc.refresh()
        r2 = await disc.refresh()  # should use cache
        assert call_count == 1
        assert r1.discovered == r2.discovered


# ---------------------------------------------------------------------------
# ModelDiscovery.refresh with fake replicate
# ---------------------------------------------------------------------------


class TestModelDiscoveryRefresh:
    def setup_method(self) -> None:
        _remove_fake_replicate()

    def teardown_method(self) -> None:
        _remove_fake_replicate()

    @pytest.mark.asyncio
    async def test_refresh_registers_models(self) -> None:
        models = [_make_model("meta", "llama"), _make_model("mistral", "mixtral")]
        _install_fake_replicate(models)

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))

        result = await disc.refresh()
        assert result.registered == 2
        assert result.discovered == 2
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_refresh_respects_max_models(self) -> None:
        models = [_make_model("m", f"model{i}") for i in range(10)]
        _install_fake_replicate(models)

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(max_models=3, ttl_seconds=0))

        result = await disc.refresh()
        assert result.discovered == 3

    @pytest.mark.asyncio
    async def test_refresh_updates_existing(self) -> None:
        models = [_make_model("meta", "llama")]
        _install_fake_replicate(models)

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))

        await disc.refresh()
        _install_fake_replicate(models)
        disc._last_refresh = 0.0  # force refresh
        disc._last_result = None
        result = await disc.refresh()
        assert result.updated == 1

    @pytest.mark.asyncio
    async def test_refresh_without_replicate_returns_error(self) -> None:
        import sys

        sys.modules.pop("replicate", None)

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))

        result = await disc.refresh()
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_refresh_api_failure_records_error(self) -> None:
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

        result = await disc.refresh()
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Background refresh
# ---------------------------------------------------------------------------


class TestBackgroundRefresh:
    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        disc = ModelDiscovery(
            AgentRegistry(),
            DiscoveryConfig(
                ttl_seconds=0,
                background_interval_seconds=0.05,
            ),
        )

        async def _fake_fetch(*, api_token: str | None) -> DiscoveryResult:
            return DiscoveryResult(discovered=1)

        disc._fetch_and_register = _fake_fetch  # type: ignore[method-assign]

        task = disc.start_background_refresh()
        assert not task.done()
        await asyncio.sleep(0.12)
        disc.stop_background_refresh()
        # Should have refreshed at least once
        assert disc.last_result is not None

    def test_start_raises_when_interval_zero(self) -> None:
        disc = ModelDiscovery(AgentRegistry(), DiscoveryConfig(background_interval_seconds=0))
        with pytest.raises(ValueError, match="background_interval_seconds"):
            disc.start_background_refresh()

    @pytest.mark.asyncio
    async def test_start_returns_same_task_if_running(self) -> None:
        disc = ModelDiscovery(
            AgentRegistry(),
            DiscoveryConfig(
                ttl_seconds=0,
                background_interval_seconds=0.1,
            ),
        )

        async def _fake_fetch(*, api_token: str | None) -> DiscoveryResult:
            return DiscoveryResult()

        disc._fetch_and_register = _fake_fetch  # type: ignore[method-assign]

        t1 = disc.start_background_refresh()
        t2 = disc.start_background_refresh()
        assert t1 is t2
        disc.stop_background_refresh()

    def test_stop_noop_when_not_running(self) -> None:
        disc = ModelDiscovery(AgentRegistry())
        disc.stop_background_refresh()  # Should not raise

    @pytest.mark.asyncio
    async def test_background_loop_exception_is_logged(self) -> None:
        """Exception in background loop is caught and logged (lines 333-334)."""
        disc = ModelDiscovery(
            AgentRegistry(),
            DiscoveryConfig(
                ttl_seconds=0,
                background_interval_seconds=0.05,
            ),
        )

        call_count = 0

        async def _failing_fetch(*, api_token: str | None) -> DiscoveryResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")
            return DiscoveryResult(discovered=1)

        disc._fetch_and_register = _failing_fetch  # type: ignore[method-assign]

        disc.start_background_refresh()
        await asyncio.sleep(0.15)
        disc.stop_background_refresh()
        # The loop should have recovered and succeeded on the second attempt
        assert call_count >= 2


# ---------------------------------------------------------------------------
# discover_and_register
# ---------------------------------------------------------------------------


class TestDiscoverAndRegister:
    def setup_method(self) -> None:
        _remove_fake_replicate()

    def teardown_method(self) -> None:
        _remove_fake_replicate()

    @pytest.mark.asyncio
    async def test_creates_registry_when_none(self) -> None:
        import sys

        sys.modules.pop("replicate", None)

        reg, result = await discover_and_register()
        assert isinstance(reg, AgentRegistry)
        assert len(result.errors) > 0  # no replicate installed

    @pytest.mark.asyncio
    async def test_uses_provided_registry(self) -> None:
        import sys

        sys.modules.pop("replicate", None)

        existing = AgentRegistry()

        reg, _ = await discover_and_register(registry=existing)
        assert reg is existing


class TestModelDiscoveryRegistryProperty:
    """Cover the registry property (line 233)."""

    def test_registry_property_returns_init_registry(self) -> None:
        reg = AgentRegistry()
        disc = ModelDiscovery(reg)
        assert disc.registry is reg


class TestModelDiscoverySkippedAndImportError:
    """Cover skipped counter (lines 302-304) and ImportError (line 316)."""

    def setup_method(self) -> None:
        _remove_fake_replicate()

    def teardown_method(self) -> None:
        _remove_fake_replicate()

    @pytest.mark.asyncio
    async def test_skipped_models_increment_counter(self) -> None:
        """Models that fail _model_to_metadata are counted as skipped (lines 302-304)."""
        # One valid model, one that will be skipped (empty owner)
        valid = _make_model("meta", "llama")
        bad = SimpleNamespace(owner="", name="", description="x", tags=[])
        _install_fake_replicate([valid, bad])

        reg = AgentRegistry()
        disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))

        result = await disc.refresh()
        assert result.skipped == 1
        assert result.discovered == 1

    @pytest.mark.asyncio
    async def test_import_error_branch(self) -> None:
        """When replicate SDK is not importable, ImportError is caught (line 316)."""
        import sys

        # Ensure replicate is not importable at all
        sys.modules.pop("replicate", None)

        # Also prevent import from finding it on disk
        import builtins

        real_import = builtins.__import__

        def _blocking_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "replicate" or name.startswith("replicate."):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = _blocking_import  # type: ignore[assignment]
        try:
            reg = AgentRegistry()
            disc = ModelDiscovery(reg, DiscoveryConfig(ttl_seconds=0))
            result = await disc.refresh()
            assert len(result.errors) > 0
            assert any("replicate SDK not installed" in e for e in result.errors)
        finally:
            builtins.__import__ = real_import
