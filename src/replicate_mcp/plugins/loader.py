"""Entry-point based plugin discovery and loading.

Plugins declare themselves in their ``pyproject.toml``::

    [project.entry-points."replicate_mcp.plugins"]
    my_plugin = "my_package.plugin:MyPlugin"

:func:`load_plugins` discovers all such entry points and instantiates
the registered classes.
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from replicate_mcp.plugins.base import BasePlugin, PluginError

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "replicate_mcp.plugins"


def load_plugins(
    *,
    extra_classes: list[type[BasePlugin]] | None = None,
    skip_names: set[str] | None = None,
) -> list[BasePlugin]:
    """Discover and instantiate all registered plugins.

    Plugins are discovered via the ``replicate_mcp.plugins`` entry-point
    group.  Additional plugin classes can be injected directly through
    *extra_classes* (useful in tests without installing a package).

    Args:
        extra_classes: Plugin classes to instantiate directly (bypasses
                       entry-point discovery for those classes).
        skip_names:    Plugin names to skip even if discovered.

    Returns:
        List of initialised :class:`BasePlugin` instances.  Plugins
        that fail to instantiate are logged and skipped.
    """
    skip = skip_names or set()
    instances: list[BasePlugin] = []

    # --- entry-point discovery ---
    eps = entry_points(group=_ENTRY_POINT_GROUP)
    for ep in eps:
        if ep.name in skip:
            logger.debug("Skipping plugin %r (in skip list)", ep.name)
            continue
        try:
            cls: type[BasePlugin] = ep.load()
            instance = _instantiate(cls)
            if instance is not None:
                instances.append(instance)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load plugin %r: %s", ep.name, exc)

    # --- directly provided classes ---
    for cls in extra_classes or []:
        instance = _instantiate(cls)
        if instance is not None:
            instances.append(instance)

    logger.info("Loaded %d plugin(s): %s", len(instances), [p.name for p in instances])
    return instances


def load_plugin_from_path(module_path: str, class_name: str) -> BasePlugin:
    """Dynamically load a single plugin by dotted module path and class name.

    Args:
        module_path: Dotted import path, e.g. ``"mypkg.plugins.logging"``.
        class_name:  Class name within the module, e.g. ``"LoggingPlugin"``.

    Returns:
        An initialised :class:`BasePlugin` instance.

    Raises:
        :class:`PluginError`: If the module cannot be imported or the
            class cannot be instantiated.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise PluginError(class_name, f"cannot import module '{module_path}': {exc}") from exc

    cls = getattr(module, class_name, None)
    if cls is None:
        raise PluginError(class_name, f"class '{class_name}' not found in '{module_path}'")

    instance = _instantiate(cls)
    if instance is None:
        raise PluginError(class_name, "instantiation returned None")
    return instance


def _instantiate(cls: type[BasePlugin]) -> BasePlugin | None:
    """Safely instantiate a plugin class.

    Returns ``None`` and logs a warning if instantiation fails.
    """
    if not (isinstance(cls, type) and issubclass(cls, BasePlugin)):
        logger.warning("Not a BasePlugin subclass: %r", cls)
        return None
    try:
        return cls()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not instantiate plugin %r: %s", cls.__name__, exc)
        return None


__all__ = [
    "load_plugins",
    "load_plugin_from_path",
]
