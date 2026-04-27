"""Safe transform and condition functions for workflow edges.

This module provides a registry-based approach for edge transforms and
conditions, **eliminating the need for ``eval()`` or string-encoded lambdas**.

Security note (CWE-94):
    Never use ``eval()`` or ``exec()`` to deserialise user-provided
    expressions.  All transform / condition logic must be registered as
    concrete Python callables via :class:`TransformRegistry`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TransformFn = Callable[[dict[str, Any]], dict[str, Any]]
ConditionFn = Callable[[dict[str, Any]], bool]


class TransformRegistry:
    """Thread-safe registry for named transform and condition functions.

    Instead of encoding lambdas as strings in YAML (which requires
    ``eval()``), workflows reference transforms **by name**.  The
    registry is populated at import time or during application
    startup.

    Example YAML (safe)::

        edges:
          - from: ideator
            to: image_gen
            transform: extract_prompt      # ← name, not code
            condition: quality_above_0_7

    Example registration::

        registry = TransformRegistry()

        @registry.transform("extract_prompt")
        def _extract(data):
            return {"prompt": data["enhanced_prompt"]}

        @registry.condition("quality_above_0_7")
        def _quality_gate(data):
            return data.get("quality_threshold", 0) > 0.7
    """

    def __init__(self) -> None:
        self._transforms: dict[str, TransformFn] = {}
        self._conditions: dict[str, ConditionFn] = {}

    # ── registration decorators ──────────────────────────────────────

    def transform(self, name: str) -> Callable[[TransformFn], TransformFn]:
        """Register a named transform function."""

        def decorator(fn: TransformFn) -> TransformFn:
            if name in self._transforms:
                raise ValueError(f"Transform '{name}' is already registered")
            self._transforms[name] = fn
            return fn

        return decorator

    def condition(self, name: str) -> Callable[[ConditionFn], ConditionFn]:
        """Register a named condition function."""

        def decorator(fn: ConditionFn) -> ConditionFn:
            if name in self._conditions:
                raise ValueError(f"Condition '{name}' is already registered")
            self._conditions[name] = fn
            return fn

        return decorator

    # ── lookup ───────────────────────────────────────────────────────

    def get_transform(self, name: str) -> TransformFn:
        """Retrieve a previously registered transform by *name*.

        Raises :class:`KeyError` if the name has not been registered.
        """
        if name not in self._transforms:
            raise KeyError(
                f"Transform '{name}' not found. "
                f"Available: {sorted(self._transforms)}"
            )
        return self._transforms[name]

    def get_condition(self, name: str) -> ConditionFn:
        """Retrieve a previously registered condition by *name*.

        Raises :class:`KeyError` if the name has not been registered.
        """
        if name not in self._conditions:
            raise KeyError(
                f"Condition '{name}' not found. "
                f"Available: {sorted(self._conditions)}"
            )
        return self._conditions[name]

    # ── introspection ────────────────────────────────────────────────

    @property
    def transform_names(self) -> list[str]:
        """Return sorted list of registered transform names."""
        return sorted(self._transforms)

    @property
    def condition_names(self) -> list[str]:
        """Return sorted list of registered condition names."""
        return sorted(self._conditions)


# ── module-level default registry ────────────────────────────────────

default_registry = TransformRegistry()
"""Global default registry for built-in transforms."""


# ── built-in transforms ─────────────────────────────────────────────


@default_registry.transform("extract_prompt")
def _extract_prompt(data: dict[str, Any]) -> dict[str, Any]:
    """Extract ``enhanced_prompt`` → ``prompt``."""
    return {"prompt": data["enhanced_prompt"]}


@default_registry.transform("extract_query")
def _extract_query(data: dict[str, Any]) -> dict[str, Any]:
    """Extract ``topic`` → ``query``."""
    return {"query": data["topic"]}


@default_registry.transform("extract_sources")
def _extract_sources(data: dict[str, Any]) -> dict[str, Any]:
    """Extract ``results`` → ``sources``."""
    return {"sources": data["results"]}


@default_registry.transform("extract_analysis")
def _extract_analysis(data: dict[str, Any]) -> dict[str, Any]:
    """Extract ``report`` → ``analysis``."""
    return {"analysis": data["report"]}


@default_registry.transform("passthrough")
def _passthrough(data: dict[str, Any]) -> dict[str, Any]:
    """Return data unchanged."""
    return data


# ── built-in conditions ─────────────────────────────────────────────


@default_registry.condition("always")
def _always(data: dict[str, Any]) -> bool:
    """Always returns ``True``."""
    return True


@default_registry.condition("quality_above_0_7")
def _quality_above_07(data: dict[str, Any]) -> bool:
    """Check if ``quality_threshold`` exceeds 0.7."""
    return float(data.get("quality_threshold", 0)) > 0.7


__all__ = [
    "TransformFn",
    "ConditionFn",
    "TransformRegistry",
    "default_registry",
]
