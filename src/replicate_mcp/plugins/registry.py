"""Runtime registry for active plugins.

Maintains the lifecycle (setup/teardown) and provides a dispatch
surface so the executor can call plugin hooks without knowing the
concrete plugin types.
"""

from __future__ import annotations

import logging
from typing import Any

from replicate_mcp.plugins.base import BasePlugin, PluginError

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Maintains a collection of active :class:`BasePlugin` instances.

    Lifecycle:

    1. Call :meth:`load` to add a plugin — ``setup()`` is called.
    2. Use :meth:`dispatch_run` / :meth:`dispatch_result` / :meth:`dispatch_error`
       during execution.
    3. Call :meth:`unload` or :meth:`unload_all` — ``teardown()`` is called.

    Example::

        registry = PluginRegistry()
        registry.load(MyPlugin())

        # During execution:
        registry.dispatch_run("llama3_chat", {"prompt": "hi"})
        registry.dispatch_result("llama3_chat", chunks, latency_ms=42.0)

        registry.unload_all()
    """

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}

    # ---- lifecycle ----

    def load(self, plugin: BasePlugin) -> None:
        """Add *plugin* to the registry and call :meth:`BasePlugin.setup`.

        Raises:
            :class:`PluginError`: If a plugin with the same name is
                already loaded.
        """
        name = plugin.name
        if name in self._plugins:
            raise PluginError(name, "already loaded — unload it first")
        try:
            plugin.setup()
        except Exception as exc:  # noqa: BLE001
            raise PluginError(name, f"setup() raised: {exc}") from exc
        self._plugins[name] = plugin
        logger.info("Plugin loaded: %r", name)

    def load_many(self, plugins: list[BasePlugin]) -> None:
        """Convenience wrapper to :meth:`load` multiple plugins at once."""
        for plugin in plugins:
            self.load(plugin)

    def unload(self, name: str) -> None:
        """Remove and teardown the plugin named *name*.

        Raises:
            :class:`PluginError`: If no plugin with that name is loaded.
        """
        plugin = self._plugins.pop(name, None)
        if plugin is None:
            raise PluginError(name, "not loaded")
        self._teardown_safe(plugin)
        logger.info("Plugin unloaded: %r", name)

    def unload_all(self) -> None:
        """Teardown and remove all plugins."""
        for name in list(self._plugins):
            plugin = self._plugins.pop(name)
            self._teardown_safe(plugin)
        logger.info("All plugins unloaded")

    # ---- query ----

    def get(self, name: str) -> BasePlugin | None:
        """Return the plugin named *name*, or ``None``."""
        return self._plugins.get(name)

    def has(self, name: str) -> bool:
        """Return ``True`` if a plugin named *name* is loaded."""
        return name in self._plugins

    @property
    def names(self) -> list[str]:
        """Names of all currently loaded plugins."""
        return list(self._plugins)

    @property
    def count(self) -> int:
        """Number of loaded plugins."""
        return len(self._plugins)

    # ---- hooks dispatch ----

    def dispatch_run(
        self,
        agent_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call :meth:`~BasePlugin.on_agent_run` on all plugins in load order.

        Each plugin may return a replacement payload dict (mutable middleware).
        Returning ``None`` passes the payload through unchanged.  Plugins are
        applied sequentially so transformations compose correctly.

        Args:
            agent_name: The agent being invoked.
            payload:    The original input payload.

        Returns:
            The (potentially transformed) payload after all plugins have run.
        """
        for plugin in self._plugins.values():
            try:
                result = plugin.on_agent_run(agent_name, payload)
                if result is not None:
                    payload = result
            except Exception as exc:  # noqa: BLE001
                logger.warning("Plugin %r on_agent_run raised: %s", plugin.name, exc)
        return payload

    def dispatch_result(
        self,
        agent_name: str,
        chunks: list[dict[str, Any]],
        latency_ms: float,
    ) -> list[dict[str, Any]]:
        """Call :meth:`~BasePlugin.on_agent_result` on all plugins in load order.

        Each plugin may return a replacement chunk list (mutable middleware).
        Returning ``None`` passes the chunk list through unchanged.  Plugins
        are applied sequentially so transformations compose correctly.

        Args:
            agent_name: The agent that was invoked.
            chunks:     The output chunks produced by the executor.
            latency_ms: Wall-clock duration of the invocation in milliseconds.

        Returns:
            The (potentially transformed) chunk list after all plugins have run.
        """
        for plugin in self._plugins.values():
            try:
                result = plugin.on_agent_result(agent_name, chunks, latency_ms)
                if result is not None:
                    chunks = result
            except Exception as exc:  # noqa: BLE001
                logger.warning("Plugin %r on_agent_result raised: %s", plugin.name, exc)
        return chunks

    def dispatch_error(
        self,
        agent_name: str,
        error: Exception,
    ) -> None:
        """Call :meth:`~BasePlugin.on_error` on all plugins."""
        for plugin in self._plugins.values():
            try:
                plugin.on_error(agent_name, error)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Plugin %r on_error raised: %s", plugin.name, exc)

    # ---- private ----

    @staticmethod
    def _teardown_safe(plugin: BasePlugin) -> None:
        try:
            plugin.teardown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Plugin %r teardown() raised: %s", plugin.name, exc)

    def __repr__(self) -> str:
        return f"PluginRegistry(plugins={self.names!r})"


__all__ = ["PluginRegistry"]
