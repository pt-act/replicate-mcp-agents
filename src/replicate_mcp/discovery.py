"""Dynamic model discovery from the Replicate API catalog.

Sprint S9 — Differentiation.  Provides auto-discovery of models from
the Replicate API with TTL-based caching and automatic registration
into the :class:`~replicate_mcp.agents.registry.AgentRegistry`.

Key features:

* :class:`ModelDiscovery` — async discovery with configurable TTL.
* :class:`DiscoveryConfig` — filter by owner/tags, page size, TTL.
* :func:`discover_and_register` — one-shot convenience wrapper.
* Background refresh loop via :meth:`ModelDiscovery.start_background_refresh`.

Design (see ADR-006):
    - Discovery is *non-blocking*: on cache hit the call returns
      instantly from the local snapshot.
    - Results are merged into the registry with ``register_or_update``
      so existing customisations (tags, cost overrides) survive a
      refresh cycle.
    - The Replicate SDK is imported lazily so the package is importable
      even when the API token is absent.

Usage::

    from replicate_mcp.discovery import ModelDiscovery, DiscoveryConfig
    from replicate_mcp.agents.registry import AgentRegistry

    cfg  = DiscoveryConfig(owner="meta", max_models=20, ttl_seconds=300)
    reg  = AgentRegistry()
    disc = ModelDiscovery(registry=reg, config=cfg)

    count = await disc.refresh(api_token="r8_...")
    print(f"Discovered {count} models")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryConfig:
    """Parameters controlling the model-discovery process.

    Attributes:
        owner:          If set, only models from this Replicate owner
                        are discovered (e.g. ``"meta"``).
        required_tags:  Whitelist of tags; a model is included only if
                        it has *at least one* of these tags.  Empty
                        means "include all".
        max_models:     Hard cap on the number of models discovered per
                        refresh cycle.  Prevents unbounded memory use.
        ttl_seconds:    Minimum seconds between API calls.  A refresh
                        that happens sooner than ``ttl_seconds`` after
                        the last successful one is skipped.
        auto_streaming: If ``True``, discovered models are registered
                        with ``supports_streaming = True`` by default.
        background_interval_seconds:
                        How often the background task calls
                        :meth:`ModelDiscovery.refresh` automatically.
                        ``0`` disables the background loop.
    """

    owner: str | None = None
    required_tags: list[str] = field(default_factory=list)
    max_models: int = 50
    ttl_seconds: float = 300.0
    auto_streaming: bool = True
    background_interval_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Discovery result
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryResult:
    """Summary of a single discovery pass.

    Attributes:
        discovered:  Models found from the API in this refresh.
        registered:  Models newly added to the registry.
        updated:     Models already in the registry that were refreshed.
        skipped:     Models excluded by filters.
        errors:      Non-fatal errors encountered during discovery.
        elapsed_ms:  Wall-clock time for the discovery call.
    """

    discovered: int = 0
    registered: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def total_registered(self) -> int:
        """Registered + updated."""
        return self.registered + self.updated


# ---------------------------------------------------------------------------
# Model-to-metadata conversion
# ---------------------------------------------------------------------------


def _model_to_metadata(model: Any, config: DiscoveryConfig) -> AgentMetadata | None:  # noqa: ANN401
    """Convert a Replicate model object to :class:`AgentMetadata`.

    Returns ``None`` if the model should be skipped (does not pass
    filter criteria).
    """
    try:
        owner: str = getattr(model, "owner", None) or ""
        name: str = getattr(model, "name", None) or ""
        if not owner or not name:
            return None

        # Owner filter
        if config.owner and owner != config.owner:
            return None

        # Tag filter
        model_tags: list[str] = list(getattr(model, "tags", None) or [])
        if config.required_tags and not any(t in model_tags for t in config.required_tags):
            return None

        safe_name = f"{owner}__{name}".replace("-", "_").replace("/", "__")
        replicate_model = f"{owner}/{name}"
        description: str = (
            getattr(model, "description", None)
            or f"Auto-discovered Replicate model {replicate_model}"
        )

        return AgentMetadata(
            safe_name=safe_name,
            description=description,
            model=replicate_model,
            supports_streaming=config.auto_streaming,
            tags=["auto-discovered"] + model_tags,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not convert model to metadata: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main discovery class
# ---------------------------------------------------------------------------


class ModelDiscovery:
    """Discovers and registers Replicate models into an :class:`AgentRegistry`.

    Args:
        registry:   Target registry where discovered agents are stored.
        config:     Discovery parameters (owner filter, TTL, etc.).
    """

    def __init__(
        self,
        registry: AgentRegistry,
        config: DiscoveryConfig | None = None,
    ) -> None:
        self._registry = registry
        self._config = config or DiscoveryConfig()
        self._last_refresh: float = 0.0
        self._last_result: DiscoveryResult | None = None
        self._background_task: asyncio.Task[None] | None = None

    # ---- public API ----

    async def refresh(self, *, api_token: str | None = None) -> DiscoveryResult:
        """Fetch models from Replicate and register them.

        Respects the configured TTL — if the last refresh happened
        fewer than ``config.ttl_seconds`` ago, returns the cached
        :class:`DiscoveryResult` immediately without making an API call.

        Args:
            api_token: Replicate API token.  Falls back to the
                       ``REPLICATE_API_TOKEN`` environment variable
                       when ``None``.

        Returns:
            :class:`DiscoveryResult` describing what was discovered.
        """
        if self._is_fresh():
            logger.debug("Discovery cache is fresh; skipping API call")
            return self._last_result or DiscoveryResult()

        t0 = time.perf_counter()
        result = await self._fetch_and_register(api_token=api_token)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        self._last_refresh = time.monotonic()
        self._last_result = result

        logger.info(
            "Discovery complete: %d discovered, %d registered, %d updated in %.1fms",
            result.discovered,
            result.registered,
            result.updated,
            result.elapsed_ms,
        )
        return result

    def is_fresh(self) -> bool:
        """Return ``True`` if the cached snapshot is still within TTL."""
        return self._is_fresh()

    @property
    def last_result(self) -> DiscoveryResult | None:
        """The most recent :class:`DiscoveryResult`, or ``None``."""
        return self._last_result

    @property
    def registry(self) -> AgentRegistry:
        """The target :class:`AgentRegistry`."""
        return self._registry

    # ---- background refresh ----

    def start_background_refresh(
        self,
        *,
        api_token: str | None = None,
    ) -> asyncio.Task[None]:
        """Start a background task that periodically calls :meth:`refresh`.

        The interval is read from
        :attr:`DiscoveryConfig.background_interval_seconds`.  If the
        interval is ``0``, a ``ValueError`` is raised.

        Returns the created :class:`asyncio.Task`.  Cancel it to stop
        the background loop::

            task = disc.start_background_refresh(api_token="r8_...")
            ...
            task.cancel()
        """
        interval = self._config.background_interval_seconds
        if interval <= 0:
            raise ValueError(
                "background_interval_seconds must be > 0 to start background refresh"
            )
        if self._background_task and not self._background_task.done():
            return self._background_task

        self._background_task = asyncio.create_task(
            self._background_loop(api_token=api_token, interval=interval),
            name="model-discovery-refresh",
        )
        return self._background_task

    def stop_background_refresh(self) -> None:
        """Cancel the background refresh task if it is running."""
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            self._background_task = None

    # ---- internals ----

    def _is_fresh(self) -> bool:
        if self._last_result is None:
            return False
        age = time.monotonic() - self._last_refresh
        return age < self._config.ttl_seconds

    async def _fetch_and_register(
        self,
        *,
        api_token: str | None,
    ) -> DiscoveryResult:
        """Call the Replicate API and merge results into the registry."""
        result = DiscoveryResult()
        cfg = self._config

        try:
            import replicate as _replicate  # noqa: PLC0415

            client = _replicate.Client(bearer_token=api_token)

            for model in client.models.list():
                if result.discovered >= cfg.max_models:
                    break

                meta = _model_to_metadata(model, cfg)
                if meta is None:
                    result.skipped += 1
                    continue

                result.discovered += 1
                already_registered = self._registry.has(meta.safe_name)
                self._registry.register_or_update(meta)

                if already_registered:
                    result.updated += 1
                else:
                    result.registered += 1

        except ImportError:
            result.errors.append("replicate SDK not installed; cannot discover models")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"Discovery API call failed: {exc}")
            logger.warning("Model discovery failed: %s", exc)

        return result

    async def _background_loop(
        self,
        *,
        api_token: str | None,
        interval: float,
    ) -> None:
        """Periodically refresh the model list in the background."""
        while True:
            try:
                await self.refresh(api_token=api_token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Background discovery error: %s", exc)
            await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


async def discover_and_register(
    *,
    api_token: str | None = None,
    config: DiscoveryConfig | None = None,
    registry: AgentRegistry | None = None,
) -> tuple[AgentRegistry, DiscoveryResult]:
    """One-shot discovery: create a registry, discover models, and return both.

    Args:
        api_token: Replicate API token.
        config:    Discovery configuration; defaults used if ``None``.
        registry:  Existing registry to populate.  A new one is created
                   if ``None``.

    Returns:
        Tuple of ``(registry, result)`` where *registry* contains all
        discovered models and *result* describes the discovery pass.

    Example::

        registry, result = await discover_and_register(api_token="r8_...")
        print(f"Registered {result.registered} models")
    """
    reg = registry or AgentRegistry()
    disc = ModelDiscovery(registry=reg, config=config)
    result = await disc.refresh(api_token=api_token)
    return reg, result


__all__ = [
    "DiscoveryConfig",
    "DiscoveryResult",
    "ModelDiscovery",
    "discover_and_register",
]
