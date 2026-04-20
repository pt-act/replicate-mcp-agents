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

    def on_agent_run(self, agent_name: str, payload: dict[str, Any]) -> None:
        self.runs.append(agent_name)

    def on_agent_result(
        self,
        agent_name: str,
        chunks: list[dict[str, Any]],
        latency_ms: float,
    ) -> None:
        self.results.append(agent_name)

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

    def on_agent_run(self, agent_name: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("hook error")

    def on_agent_result(
        self, agent_name: str, chunks: list[dict[str, Any]], latency_ms: float
    ) -> None:
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
