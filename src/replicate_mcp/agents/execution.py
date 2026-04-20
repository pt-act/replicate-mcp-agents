"""Agent execution helpers.

Provides :class:`AgentExecutor` which invokes Replicate models and
streams results back as an async iterator.

Features (Phase 2 hardened):
    - Concurrency limiter (semaphore) to cap parallel calls
    - :class:`~replicate_mcp.resilience.CircuitBreaker` per model
    - :class:`~replicate_mcp.resilience.RetryConfig` exponential back-off
    - :class:`~replicate_mcp.ratelimit.TokenBucket` rate limiting
    - :class:`~replicate_mcp.observability.Observability` OTEL traces+metrics
    - Model catalogue hydration delegated to :class:`~replicate_mcp.discovery.ModelDiscovery`
    - Streaming and non-streaming output paths

Phase 4 consolidation:
    :class:`ModelCatalogue` is retained for backward compatibility but now
    delegates its discovery logic to :class:`~replicate_mcp.discovery.ModelDiscovery`.
    New code should pass a ``ModelDiscovery`` instance to :class:`AgentExecutor`
    directly via the ``discovery`` constructor parameter.
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anyio

from replicate_mcp.exceptions import (
    ExecutionError,
    ModelNotFoundError,
)
from replicate_mcp.observability import Observability, default_observability
from replicate_mcp.ratelimit import TokenBucket
from replicate_mcp.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    RetryConfig,
    compute_retry_delay,
)

if TYPE_CHECKING:
    from replicate_mcp.cache import ResultCache
    from replicate_mcp.discovery import ModelDiscovery
    from replicate_mcp.plugins.registry import PluginRegistry
    from replicate_mcp.utils.audit import AuditLogger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default model map (static fallback)
# ---------------------------------------------------------------------------

DEFAULT_MODEL_MAP: dict[str, str] = {
    "llama3_chat": "meta/meta-llama-3-70b-instruct",
    "flux_pro": "black-forest-labs/flux-1.1-pro",
    "sdxl": "stability-ai/sdxl",
}


# ---------------------------------------------------------------------------
# ModelInfo — lightweight cache entry used by ModelCatalogue
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    """Cached metadata about a Replicate model."""

    owner: str
    name: str
    description: str = ""
    default_input_schema: dict[str, Any] = field(default_factory=dict)
    supports_streaming: bool = False


# ---------------------------------------------------------------------------
# ModelCatalogue — deprecated; delegates to ModelDiscovery internally
# ---------------------------------------------------------------------------


class ModelCatalogue:
    """In-memory cache of Replicate model metadata.

    .. deprecated::
        Use :class:`~replicate_mcp.discovery.ModelDiscovery` directly.
        ``ModelCatalogue`` is retained for backward compatibility only.
        Pass a :class:`~replicate_mcp.discovery.ModelDiscovery` instance
        to :class:`AgentExecutor` via the ``discovery`` parameter instead.

    Call :meth:`discover` to populate from the API.  Falls back to
    ``DEFAULT_MODEL_MAP`` if the API is unavailable.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._ttl_seconds: float = 300.0
        self._last_refresh: float = 0.0
        # Internal ModelDiscovery delegate (populated lazily on first discover())
        self._discovery_delegate: ModelDiscovery | None = None

    @property
    def models(self) -> dict[str, ModelInfo]:
        return dict(self._models)

    def is_stale(self) -> bool:
        return (time.monotonic() - self._last_refresh) > self._ttl_seconds

    def add(self, key: str, info: ModelInfo) -> None:
        self._models[key] = info

    def get(self, key: str) -> ModelInfo | None:
        return self._models.get(key)

    async def discover(self, *, api_token: str, limit: int = 25) -> int:
        """Fetch models from Replicate API and cache them.

        Delegates to :class:`~replicate_mcp.discovery.ModelDiscovery`
        internally.  Returns the number of models cached.  Respects the TTL.

        .. deprecated::
            Use :class:`~replicate_mcp.discovery.ModelDiscovery` instead.
        """
        warnings.warn(
            "ModelCatalogue.discover() is deprecated. "
            "Use replicate_mcp.discovery.ModelDiscovery instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not self.is_stale():
            return len(self._models)

        try:
            from replicate_mcp.agents.registry import AgentRegistry  # noqa: PLC0415
            from replicate_mcp.discovery import (  # noqa: PLC0415
                DiscoveryConfig,
                ModelDiscovery,
            )

            if self._discovery_delegate is None:
                cfg = DiscoveryConfig(max_models=limit, auto_streaming=False)
                self._discovery_delegate = ModelDiscovery(
                    registry=AgentRegistry(), config=cfg
                )

            result = await self._discovery_delegate.refresh(api_token=api_token)

            # Mirror discovered agents into the local _models cache for compat
            for name, meta in self._discovery_delegate.registry.list_agents().items():
                parts = meta.model.split("/") if meta.model else ["", name]
                owner = parts[0] if len(parts) >= 2 else ""
                model_name = parts[1] if len(parts) >= 2 else parts[0]
                self._models[f"{owner}/{model_name}"] = ModelInfo(
                    owner=owner,
                    name=model_name,
                    description=meta.description,
                    supports_streaming=meta.supports_streaming,
                )

            self._last_refresh = time.monotonic()
            return result.total_registered

        except Exception:  # noqa: BLE001
            logger.warning("Model catalogue discovery failed; using static map")
            return 0


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class AgentExecutor:
    """Execute Replicate model calls with streaming, concurrency, and retry.

    Phase 2 hardening wires in:
    - :class:`~replicate_mcp.resilience.CircuitBreaker` per model
    - :class:`~replicate_mcp.resilience.RetryConfig` exponential back-off
    - :class:`~replicate_mcp.ratelimit.TokenBucket` rate limiting
    - :class:`~replicate_mcp.observability.Observability` OTEL traces+metrics

    Usage::

        executor = AgentExecutor(max_concurrency=5)
        async for chunk in executor.run("llama3_chat", {"prompt": "Hi"}):
            print(chunk)
    """

    def __init__(
        self,
        *,
        model_map: dict[str, str] | None = None,
        api_token: str | None = None,
        max_concurrency: int = 10,
        max_retries: int = 2,
        retry_base: float = 0.5,
        catalogue: ModelCatalogue | None = None,
        discovery: ModelDiscovery | None = None,
        # Phase 2 additions
        circuit_breaker_config: CircuitBreakerConfig | None = None,
        rate_limiter: TokenBucket | None = None,
        observability: Observability | None = None,
        # Phase 5a: plugin middleware
        plugin_registry: PluginRegistry | None = None,
        # Phase 5a: audit logging
        audit_logger: AuditLogger | None = None,
        # Phase 5a: content-addressed result cache
        cache: ResultCache | None = None,
    ) -> None:
        self._model_map = model_map or dict(DEFAULT_MODEL_MAP)
        self._api_token = api_token or os.environ.get("REPLICATE_API_TOKEN", "")
        self._semaphore = anyio.Semaphore(max_concurrency)
        self._retry_config = RetryConfig(
            max_retries=max_retries,
            base_delay=retry_base,
        )
        self._catalogue = catalogue or ModelCatalogue()
        # Phase 4: canonical discovery backend (preferred over ModelCatalogue)
        self._discovery: ModelDiscovery | None = discovery
        # Phase 2: per-model circuit breakers, shared rate limiter, OTEL
        self._cb_config = circuit_breaker_config or CircuitBreakerConfig()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._rate_limiter = rate_limiter  # may be None (no rate limit)
        self._obs = observability or default_observability
        # Phase 5a: mutable middleware plugin registry
        self._plugin_registry: PluginRegistry | None = plugin_registry
        # Phase 5a: invocation audit log
        self._audit_logger: AuditLogger | None = audit_logger
        # Phase 5a: content-addressed result cache
        self._cache: ResultCache | None = cache

    @property
    def catalogue(self) -> ModelCatalogue:
        return self._catalogue

    @property
    def discovery(self) -> ModelDiscovery | None:
        """Return the :class:`~replicate_mcp.discovery.ModelDiscovery` instance if set."""
        return self._discovery

    def circuit_breaker(self, model_id: str) -> CircuitBreaker:
        """Return (creating if necessary) the circuit breaker for *model_id*."""
        if model_id not in self._breakers:
            self._breakers[model_id] = CircuitBreaker(
                name=model_id, config=self._cb_config
            )
        return self._breakers[model_id]

    def resolve_model(self, agent_id: str) -> str:
        """Map a short agent name to a full Replicate model identifier.

        Resolution order:
        1. If *agent_id* already contains ``/`` it is returned as-is.
        2. Static ``model_map`` (fast, always checked first).
        3. :class:`~replicate_mcp.discovery.ModelDiscovery` registry
           (preferred canonical source when a discovery backend is set).
        4. Legacy :class:`ModelCatalogue` (deprecated fallback).

        Raises:
            ModelNotFoundError: If the identifier cannot be resolved.
        """
        if "/" in agent_id:
            return agent_id
        if agent_id in self._model_map:
            return self._model_map[agent_id]

        # Phase 4: check the canonical ModelDiscovery registry first
        if self._discovery is not None:
            registry = self._discovery.registry
            if registry.has(agent_id):
                meta = registry.get(agent_id)
                if meta.model:
                    return meta.model
            # Also check by safe_name prefix pattern (owner__name)
            for safe_name, meta in registry.list_agents().items():
                if meta.model and meta.model.endswith(f"/{agent_id}"):
                    return meta.model

        # Legacy catalogue fallback
        for key in self._catalogue.models:
            if key.endswith(f"/{agent_id}"):
                return key

        raise ModelNotFoundError(agent_id, list(self._model_map))

    async def run(
        self,
        agent_id: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Invoke a Replicate model and yield result chunks.

        Applies a concurrency limiter, circuit breaker, rate limiter,
        and retry logic with exponential back-off + jitter.

        Yields:
            Dicts with ``agent``, ``model``, ``chunk``/``output``,
            ``latency_ms``, ``done``, and optionally ``error``.
        """
        if not self._api_token:
            yield {
                "agent": agent_id,
                "error": "REPLICATE_API_TOKEN is not set",
                "done": True,
            }
            return

        model_id = self.resolve_model(agent_id)
        breaker = self.circuit_breaker(model_id)
        last_error: Exception | None = None
        start = time.monotonic()

        # Phase 5a: allow plugin middleware to transform the input payload
        if self._plugin_registry is not None:
            payload = self._plugin_registry.dispatch_run(agent_id, payload)

        # Phase 5a: serve from cache if available (opt-in, disabled by default)
        if self._cache is not None:
            from replicate_mcp.cache import ResultCache  # noqa: PLC0415

            cache_key = ResultCache.make_key(model_id, payload)
            cached_chunks = self._cache.get(cache_key)
            if cached_chunks is not None:
                logger.debug("Cache HIT for %s — skipping API call", model_id)
                for chunk in cached_chunks:
                    yield chunk
                return
        else:
            cache_key = ""

        with self._obs.span(
            "agent.run",
            **{"agent.id": agent_id, "model.id": model_id},
        ):
            for attempt in range(self._retry_config.max_retries + 1):
                start = time.monotonic()
                try:
                    # Rate limit before each attempt
                    if self._rate_limiter is not None:
                        await self._rate_limiter.acquire()

                    # Circuit breaker check
                    breaker.pre_call()

                    async with self._semaphore:
                        needs_buffer = (
                            self._plugin_registry is not None
                            or self._cache is not None
                        )
                        if needs_buffer:
                            # Buffer chunks for plugin transforms and/or cache writes
                            collected_chunks: list[dict[str, Any]] = []
                            async for chunk in self._invoke(
                                agent_id, model_id, payload, start
                            ):
                                collected_chunks.append(chunk)

                            elapsed_ms = (time.monotonic() - start) * 1000

                            # Apply result plugin transforms (if any)
                            if self._plugin_registry is not None:
                                collected_chunks = self._plugin_registry.dispatch_result(
                                    agent_id, collected_chunks, elapsed_ms
                                )

                            # Store in cache (before yielding, in case of error)
                            if self._cache is not None and cache_key:
                                self._cache.put(cache_key, collected_chunks)

                            for chunk in collected_chunks:
                                yield chunk
                        else:
                            async for chunk in self._invoke(
                                agent_id, model_id, payload, start
                            ):
                                yield chunk

                    # Success — update breaker + record telemetry + audit
                    breaker.record_success()
                    elapsed_ms = (time.monotonic() - start) * 1000
                    self._obs.record_invocation(
                        model_id, elapsed_ms, 0.0, success=True
                    )
                    if self._audit_logger is not None:
                        self._audit_logger.record(
                            agent=agent_id,
                            model=model_id,
                            latency_ms=elapsed_ms,
                            cost_usd=0.0,
                            success=True,
                            payload=payload,
                        )
                    return

                except CircuitOpenError:
                    self._obs.record_circuit_trip(model_id)
                    elapsed_ms = (time.monotonic() - start) * 1000
                    if self._audit_logger is not None:
                        self._audit_logger.record(
                            agent=agent_id,
                            model=model_id,
                            latency_ms=elapsed_ms,
                            cost_usd=0.0,
                            success=False,
                            payload=payload,
                        )
                    yield {
                        "agent": agent_id,
                        "model": model_id,
                        "error": f"Circuit open for '{model_id}' — service unavailable",
                        "latency_ms": round(elapsed_ms, 1),
                        "done": True,
                    }
                    return

                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    breaker.record_failure()
                    elapsed_ms = (time.monotonic() - start) * 1000
                    self._obs.record_invocation(
                        model_id, elapsed_ms, 0.0, success=False
                    )
                    # Phase 5a: notify plugins of errors
                    if self._plugin_registry is not None:
                        self._plugin_registry.dispatch_error(agent_id, exc)

                    if attempt < self._retry_config.max_retries:
                        delay = compute_retry_delay(attempt, self._retry_config)
                        logger.warning(
                            "Retry %d/%d for %s after %.1fs: %s",
                            attempt + 1,
                            self._retry_config.max_retries,
                            model_id,
                            delay,
                            exc,
                        )
                        await anyio.sleep(delay)

            # All retries exhausted
            elapsed_ms = (time.monotonic() - start) * 1000
            yield {
                "agent": agent_id,
                "model": model_id,
                "error": f"{type(last_error).__name__}: {last_error}",
                "latency_ms": round(elapsed_ms, 1),
                "done": True,
            }

    async def _invoke(
        self,
        agent_id: str,
        model_id: str,
        payload: dict[str, Any],
        start: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """Single invocation attempt (no retry logic)."""

        import replicate  # noqa: S603

        try:
            output = replicate.run(model_id, input=payload)

            if hasattr(output, "__iter__") and not isinstance(output, str | bytes):
                collected: list[str] = []
                for fragment in output:
                    text = str(fragment)
                    collected.append(text)
                    yield {
                        "agent": agent_id,
                        "model": model_id,
                        "chunk": text,
                        "done": False,
                    }
                elapsed_ms = (time.monotonic() - start) * 1000
                yield {
                    "agent": agent_id,
                    "model": model_id,
                    "output": "".join(collected),
                    "latency_ms": round(elapsed_ms, 1),
                    "done": True,
                }
            else:
                elapsed_ms = (time.monotonic() - start) * 1000
                yield {
                    "agent": agent_id,
                    "model": model_id,
                    "output": str(output),
                    "latency_ms": round(elapsed_ms, 1),
                    "done": True,
                }

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.exception("Replicate call failed for %s", model_id)
            raise ExecutionError(model_id, exc) from exc


__all__ = [
    "AgentExecutor",
    "ModelCatalogue",
    "ModelInfo",
    "DEFAULT_MODEL_MAP",
]
