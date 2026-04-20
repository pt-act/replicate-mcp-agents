"""Abstract base class for replicate-mcp-agents plugins.

Every plugin must subclass :class:`BasePlugin` and implement its
abstract methods.  Non-abstract lifecycle hooks (``on_load``,
``on_unload``, etc.) have no-op defaults so plugins only override
what they care about.

Plugin API contract:

1. :meth:`BasePlugin.setup` is called once when the plugin is loaded.
2. :meth:`BasePlugin.teardown` is called once when the plugin is unloaded.
3. Hook methods (``on_agent_run``, ``on_agent_result``, ``on_error``)
   are called by the executor during normal operation.
4. Hooks must *not* raise exceptions — log and swallow.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PluginError(Exception):
    """Raised when a plugin fails to load or initialise."""

    def __init__(self, plugin_name: str, reason: str) -> None:
        super().__init__(f"Plugin '{plugin_name}' error: {reason}")
        self.plugin_name = plugin_name
        self.reason = reason


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@dataclass
class PluginMetadata:
    """Descriptive metadata for a plugin.

    Attributes:
        name:        Short, unique identifier (e.g. ``"logging"``).
        version:     Semantic version string (e.g. ``"1.0.0"``).
        description: Human-readable summary.
        author:      Plugin author name or email.
        requires:    List of Python package names this plugin depends on.
    """

    name: str
    version: str = "0.0.1"
    description: str = ""
    author: str = ""
    requires: list[str] | None = None


# ---------------------------------------------------------------------------
# BasePlugin
# ---------------------------------------------------------------------------


class BasePlugin(ABC):
    """Abstract base class that every plugin must subclass.

    Lifecycle::

        load_plugins()         → BasePlugin.__init__() → setup()
        executor.run()         → on_agent_run(), on_agent_result() / on_error()
        plugin_registry.unload() → teardown()

    Minimal example::

        class MyPlugin(BasePlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(name="my_plugin", version="1.0.0")

            def setup(self) -> None:
                print("MyPlugin loaded!")

            def teardown(self) -> None:
                print("MyPlugin unloaded!")
    """

    # ---- abstract ----

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return descriptive metadata for this plugin."""

    @abstractmethod
    def setup(self) -> None:
        """Initialise resources.  Called once after the plugin is loaded."""

    @abstractmethod
    def teardown(self) -> None:
        """Release resources.  Called once before the plugin is unloaded."""

    # ---- optional hooks ----

    def on_agent_run(
        self,
        agent_name: str,
        payload: dict[str, Any],
    ) -> None:
        """Called before each agent invocation.

        Args:
            agent_name: The ``safe_name`` of the agent being invoked.
            payload:    The input payload (read-only reference).
        """

    def on_agent_result(
        self,
        agent_name: str,
        chunks: list[dict[str, Any]],
        latency_ms: float,
    ) -> None:
        """Called after a successful agent invocation.

        Args:
            agent_name:  The ``safe_name`` of the agent.
            chunks:      The output chunks returned by the executor.
            latency_ms:  Wall-clock duration of the invocation.
        """

    def on_error(
        self,
        agent_name: str,
        error: Exception,
    ) -> None:
        """Called when an agent invocation raises an unhandled exception.

        Args:
            agent_name: The ``safe_name`` of the agent.
            error:      The exception that was raised.
        """

    # ---- helpers ----

    @property
    def name(self) -> str:
        """Convenience alias for ``self.metadata.name``."""
        return self.metadata.name

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


__all__ = [
    "BasePlugin",
    "PluginError",
    "PluginMetadata",
]
