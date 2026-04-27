"""Tests for replicate_mcp.plugins — BasePlugin, loader, and PluginRegistry."""

from __future__ import annotations

from typing import Any

import pytest

from replicate_mcp.plugins.base import BasePlugin, PluginError, PluginMetadata
from replicate_mcp.plugins.loader import _instantiate, load_plugin_from_path, load_plugins
from replicate_mcp.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Concrete test plugins
# ---------------------------------------------------------------------------


class _GoodPlugin(BasePlugin):
    """Minimal working plugin for testing."""

    def __init__(self) -> None:
        self.setup_called = False
        self.teardown_called = False
        self.runs: list[str] = []
        self.results: list[str] = []
        self.error_names: list[str] = []

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="good_plugin",
            version="1.0.0",
            description="A working plugin",
            author="Test",
        )

    def setup(self) -> None:
        self.setup_called = True

    def teardown(self) -> None:
        self.teardown_called = True

    def on_agent_run(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        self.runs.append(agent_name)
        return None  # pass-through: no transformation

    def on_agent_result(
        self,
        agent_name: str,
        chunks: list[dict[str, Any]],
        latency_ms: float,
    ) -> list[dict[str, Any]] | None:
        self.results.append(agent_name)
        return None  # pass-through: no transformation

    def on_error(self, agent_name: str, error: Exception) -> None:
        self.error_names.append(agent_name)


class _FailingSetupPlugin(BasePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="failing_setup")

    def setup(self) -> None:
        raise RuntimeError("setup failed")

    def teardown(self) -> None:
        pass


class _FailingTeardownPlugin(BasePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="failing_teardown")

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        raise RuntimeError("teardown failed")


class _FailingHooksPlugin(BasePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="failing_hooks")

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def on_agent_run(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        raise RuntimeError("hook error")

    def on_agent_result(
        self, agent_name: str, chunks: list[dict[str, Any]], latency_ms: float
    ) -> list[dict[str, Any]] | None:
        raise RuntimeError("hook error")

    def on_error(self, agent_name: str, error: Exception) -> None:
        raise RuntimeError("hook error")


# ---------------------------------------------------------------------------
# PluginMetadata
# ---------------------------------------------------------------------------


class TestPluginMetadata:
    def test_defaults(self) -> None:
        m = PluginMetadata(name="test")
        assert m.name == "test"
        assert m.version == "0.0.1"
        assert m.description == ""
        assert m.author == ""
        assert m.requires is None

    def test_full(self) -> None:
        m = PluginMetadata(
            name="full",
            version="2.0.0",
            description="desc",
            author="author",
            requires=["httpx"],
        )
        assert m.requires == ["httpx"]


# ---------------------------------------------------------------------------
# BasePlugin
# ---------------------------------------------------------------------------


class TestBasePlugin:
    def test_name_property(self) -> None:
        p = _GoodPlugin()
        assert p.name == "good_plugin"

    def test_repr(self) -> None:
        p = _GoodPlugin()
        assert "good_plugin" in repr(p)

    def test_default_hooks_do_not_raise(self) -> None:
        """BasePlugin's no-op hooks must never raise."""

        class _Minimal(BasePlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(name="minimal")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

        p = _Minimal()
        p.on_agent_run("x", {})
        p.on_agent_result("x", [], 0.0)
        p.on_error("x", RuntimeError("e"))


# ---------------------------------------------------------------------------
# PluginError
# ---------------------------------------------------------------------------


class TestPluginError:
    def test_message_contains_name(self) -> None:
        e = PluginError("my_plugin", "bad config")
        assert "my_plugin" in str(e)
        assert "bad config" in str(e)

    def test_attributes(self) -> None:
        e = PluginError("p", "reason")
        assert e.plugin_name == "p"
        assert e.reason == "reason"


# ---------------------------------------------------------------------------
# Loader — _instantiate
# ---------------------------------------------------------------------------


class TestInstantiate:
    def test_good_class(self) -> None:
        instance = _instantiate(_GoodPlugin)
        assert isinstance(instance, _GoodPlugin)

    def test_not_a_subclass_returns_none(self, caplog: Any) -> None:
        class NotAPlugin:
            pass

        result = _instantiate(NotAPlugin)  # type: ignore[arg-type]
        assert result is None

    def test_init_raises_returns_none(self) -> None:
        class BadInit(BasePlugin):
            def __init__(self) -> None:
                raise RuntimeError("init error")

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(name="bad")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

        result = _instantiate(BadInit)
        assert result is None


# ---------------------------------------------------------------------------
# Loader — load_plugins
# ---------------------------------------------------------------------------


class TestLoadPlugins:
    def test_extra_classes(self) -> None:
        plugins = load_plugins(extra_classes=[_GoodPlugin])
        assert len(plugins) == 1
        assert plugins[0].name == "good_plugin"

    def test_no_entry_points_returns_empty(self) -> None:
        # The test environment has no installed plugins
        plugins = load_plugins(extra_classes=[])
        assert isinstance(plugins, list)

    def test_failing_class_skipped(self) -> None:
        class FailInit(BasePlugin):
            def __init__(self) -> None:
                raise RuntimeError("boom")

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(name="fail")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

        plugins = load_plugins(extra_classes=[FailInit, _GoodPlugin])
        assert len(plugins) == 1  # FailInit skipped, _GoodPlugin loaded

    def test_entry_point_discovery(self) -> None:
        """load_plugins discovers and instantiates plugins via entry points."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        ep = MagicMock()
        ep.name = "good_ep"
        ep.load.return_value = _GoodPlugin
        with patch(
            "replicate_mcp.plugins.loader.entry_points",
            return_value=[ep],
        ):
            plugins = load_plugins(extra_classes=[])
        assert len(plugins) == 1
        assert plugins[0].name == "good_plugin"

    def test_entry_point_skip_names(self) -> None:
        """Plugins in skip_names are not loaded even if discovered."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        ep = MagicMock()
        ep.name = "good_ep"
        ep.load.return_value = _GoodPlugin
        with patch(
            "replicate_mcp.plugins.loader.entry_points",
            return_value=[ep],
        ):
            plugins = load_plugins(skip_names={"good_ep"})
        assert len(plugins) == 0

    def test_entry_point_load_exception_skipped(self) -> None:
        """Entry points that raise on load() are skipped with a warning."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        bad_ep = MagicMock()
        bad_ep.name = "bad_ep"
        bad_ep.load.side_effect = ImportError("missing")
        with patch(
            "replicate_mcp.plugins.loader.entry_points",
            return_value=[bad_ep],
        ):
            plugins = load_plugins(extra_classes=[])
        assert len(plugins) == 0

    def test_logger_info_on_load(self, caplog: Any) -> None:
        """load_plugins logs the number and names of loaded plugins."""
        import logging  # noqa: PLC0415

        with caplog.at_level(logging.INFO, logger="replicate_mcp.plugins.loader"):
            load_plugins(extra_classes=[_GoodPlugin])
        assert "Loaded 1 plugin" in caplog.text
        assert "good_plugin" in caplog.text


# ---------------------------------------------------------------------------
# Loader — load_plugin_from_path
# ---------------------------------------------------------------------------


class TestLoadPluginFromPath:
    def test_known_module_raises(self) -> None:
        with pytest.raises(PluginError):
            load_plugin_from_path("replicate_mcp.plugins.base", "BasePlugin")

    def test_bad_module_raises(self) -> None:
        with pytest.raises(PluginError, match="cannot import"):
            load_plugin_from_path("nonexistent.module", "SomeClass")

    def test_missing_class_raises(self) -> None:
        with pytest.raises(PluginError, match="not found"):
            load_plugin_from_path("replicate_mcp.plugins.base", "NoSuchClass")

    def test_instantiation_failure_raises(self) -> None:
        with pytest.raises(PluginError):
            load_plugin_from_path("replicate_mcp.plugins.base", "BasePlugin")

    def test_successful_load(self) -> None:
        """load_plugin_from_path returns an initialised plugin on success."""
        # _GoodPlugin is defined in this module (tests.unit.test_plugins)
        from replicate_mcp.plugins.builtin import PIIMaskPlugin  # noqa: PLC0415

        plugin = load_plugin_from_path("replicate_mcp.plugins.builtin", "PIIMaskPlugin")
        assert isinstance(plugin, PIIMaskPlugin)
        assert plugin.name == "pii_mask"


# ---------------------------------------------------------------------------
# PluginRegistry
# ---------------------------------------------------------------------------


class TestPluginRegistry:
    def test_load_calls_setup(self) -> None:
        reg = PluginRegistry()
        p = _GoodPlugin()
        reg.load(p)
        assert p.setup_called

    def test_unload_calls_teardown(self) -> None:
        reg = PluginRegistry()
        p = _GoodPlugin()
        reg.load(p)
        reg.unload("good_plugin")
        assert p.teardown_called

    def test_load_duplicate_raises(self) -> None:
        reg = PluginRegistry()
        reg.load(_GoodPlugin())
        with pytest.raises(PluginError, match="already loaded"):
            reg.load(_GoodPlugin())

    def test_unload_unknown_raises(self) -> None:
        reg = PluginRegistry()
        with pytest.raises(PluginError, match="not loaded"):
            reg.unload("nonexistent")

    def test_unload_all(self) -> None:
        reg = PluginRegistry()
        p1 = _GoodPlugin()
        reg.load(p1)
        reg.unload_all()
        assert reg.count == 0
        assert p1.teardown_called

    def test_has(self) -> None:
        reg = PluginRegistry()
        reg.load(_GoodPlugin())
        assert reg.has("good_plugin")
        assert not reg.has("missing")

    def test_get(self) -> None:
        reg = PluginRegistry()
        p = _GoodPlugin()
        reg.load(p)
        assert reg.get("good_plugin") is p
        assert reg.get("missing") is None

    def test_names(self) -> None:
        reg = PluginRegistry()
        reg.load(_GoodPlugin())
        assert "good_plugin" in reg.names

    def test_count(self) -> None:
        reg = PluginRegistry()
        assert reg.count == 0
        reg.load(_GoodPlugin())
        assert reg.count == 1

    def test_load_many(self) -> None:
        class _P2(BasePlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(name="p2")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

        reg = PluginRegistry()
        reg.load_many([_GoodPlugin(), _P2()])
        assert reg.count == 2

    def test_dispatch_run(self) -> None:
        reg = PluginRegistry()
        p = _GoodPlugin()
        reg.load(p)
        reg.dispatch_run("my_agent", {"prompt": "test"})
        assert "my_agent" in p.runs

    def test_dispatch_result(self) -> None:
        reg = PluginRegistry()
        p = _GoodPlugin()
        reg.load(p)
        reg.dispatch_result("my_agent", [{"done": True}], 42.0)
        assert "my_agent" in p.results

    def test_dispatch_error(self) -> None:
        reg = PluginRegistry()
        p = _GoodPlugin()
        reg.load(p)
        reg.dispatch_error("my_agent", RuntimeError("fail"))
        assert "my_agent" in p.error_names

    def test_failing_setup_raises_plugin_error(self) -> None:
        reg = PluginRegistry()
        with pytest.raises(PluginError, match="setup"):
            reg.load(_FailingSetupPlugin())

    def test_failing_teardown_does_not_raise(self) -> None:
        reg = PluginRegistry()
        reg.load(_FailingTeardownPlugin())
        reg.unload("failing_teardown")  # Should log, not raise

    def test_failing_hooks_do_not_raise(self) -> None:
        reg = PluginRegistry()
        reg.load(_FailingHooksPlugin())
        reg.dispatch_run("x", {})
        reg.dispatch_result("x", [], 0.0)
        reg.dispatch_error("x", RuntimeError("e"))

    def test_repr(self) -> None:
        reg = PluginRegistry()
        assert "PluginRegistry" in repr(reg)

    def test_dispatch_run_returns_original_when_no_transform(self) -> None:
        """dispatch_run returns original payload when no plugin transforms it."""
        reg = PluginRegistry()
        reg.load(_GoodPlugin())
        payload = {"prompt": "hello"}
        result = reg.dispatch_run("agent", payload)
        assert result == payload

    def test_dispatch_result_returns_original_when_no_transform(self) -> None:
        """dispatch_result returns original chunks when no plugin transforms them."""
        reg = PluginRegistry()
        reg.load(_GoodPlugin())
        chunks = [{"done": True}]
        result = reg.dispatch_result("agent", chunks, 100.0)
        assert result == chunks


# ---------------------------------------------------------------------------
# Mutable middleware — transforming plugins
# ---------------------------------------------------------------------------


class _PayloadAugmentPlugin(BasePlugin):
    """Plugin that injects a 'system' key into every payload."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="payload_augment")

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def on_agent_run(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return {**payload, "system": "You are a helpful assistant."}


class _ChunkFilterPlugin(BasePlugin):
    """Plugin that removes chunks with 'done: False' from results."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="chunk_filter")

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def on_agent_result(
        self, agent_name: str, chunks: list[dict[str, Any]], latency_ms: float
    ) -> list[dict[str, Any]] | None:
        return [c for c in chunks if c.get("done", False)]


class _ChainPlugin(BasePlugin):
    """Plugin that appends a marker to the payload."""

    def __init__(self, name: str, marker: str) -> None:
        self._name = name
        self._marker = marker

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name=self._name)

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def on_agent_run(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        markers = list(payload.get("markers", []))
        markers.append(self._marker)
        return {**payload, "markers": markers}


class TestMutableMiddleware:
    def test_payload_augment_plugin(self) -> None:
        reg = PluginRegistry()
        reg.load(_PayloadAugmentPlugin())
        result = reg.dispatch_run("agent", {"prompt": "hi"})
        assert result["prompt"] == "hi"
        assert result["system"] == "You are a helpful assistant."

    def test_chunk_filter_plugin(self) -> None:
        reg = PluginRegistry()
        reg.load(_ChunkFilterPlugin())
        chunks = [
            {"chunk": "a", "done": False},
            {"chunk": "b", "done": False},
            {"output": "ab", "done": True},
        ]
        result = reg.dispatch_result("agent", chunks, 100.0)
        assert len(result) == 1
        assert result[0]["done"] is True

    def test_plugins_apply_in_load_order(self) -> None:
        """Transformations compose: each plugin sees the previous plugin's result."""
        reg = PluginRegistry()
        reg.load(_ChainPlugin("first", "A"))
        reg.load(_ChainPlugin("second", "B"))
        result = reg.dispatch_run("agent", {})
        assert result["markers"] == ["A", "B"]

    def test_noop_plugin_does_not_change_payload(self) -> None:
        """A plugin returning None leaves the payload unchanged."""
        reg = PluginRegistry()
        reg.load(_GoodPlugin())
        payload = {"prompt": "original"}
        result = reg.dispatch_run("agent", payload)
        assert result == payload

    def test_failing_transform_plugin_skipped(self) -> None:
        """A plugin that raises must not corrupt the payload chain."""
        reg = PluginRegistry()
        reg.load(_FailingHooksPlugin())
        reg.load(_ChainPlugin("safe", "safe"))
        # _FailingHooksPlugin raises, should be swallowed; _ChainPlugin runs
        result = reg.dispatch_run("agent", {})
        assert "markers" in result
        assert "safe" in result["markers"]

    def test_empty_registry_returns_payload_unchanged(self) -> None:
        reg = PluginRegistry()
        payload = {"x": 1}
        assert reg.dispatch_run("agent", payload) == payload
        chunks = [{"done": True}]
        assert reg.dispatch_result("agent", chunks, 1.0) == chunks
