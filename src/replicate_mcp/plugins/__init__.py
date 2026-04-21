"""Plugin ecosystem for replicate-mcp-agents.

Sprint S10 — Differentiation.  Provides a lightweight, ``pip``-installable
plugin system based on Python `entry points`_.

Third-party plugins declare themselves in their ``pyproject.toml``::

    [project.entry-points."replicate_mcp.plugins"]
    my_plugin = "my_package.plugin:MyPlugin"

and are discovered automatically by :func:`load_plugins`.

Submodules:

* :mod:`~replicate_mcp.plugins.base`      — :class:`BasePlugin` ABC.
* :mod:`~replicate_mcp.plugins.loader`    — entry-point discovery.
* :mod:`~replicate_mcp.plugins.registry`  — runtime plugin registry.
* :mod:`~replicate_mcp.plugins.builtin`   — built-in plugins shipped
  with the package.

.. _entry points:
   https://packaging.python.org/en/latest/specifications/entry-points/
"""

from replicate_mcp.plugins.base import BasePlugin, PluginError, PluginMetadata
from replicate_mcp.plugins.builtin import (
    ContentFilterPlugin,
    CostCapPlugin,
    PIIMaskPlugin,
)
from replicate_mcp.plugins.loader import load_plugins
from replicate_mcp.plugins.registry import PluginRegistry

__all__ = [
    "BasePlugin",
    "PluginError",
    "PluginMetadata",
    "PluginRegistry",
    "load_plugins",
    # Built-in guardrail plugins (Phase 6)
    "PIIMaskPlugin",
    "ContentFilterPlugin",
    "CostCapPlugin",
]
