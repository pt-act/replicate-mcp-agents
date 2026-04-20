"""Agent execution helpers.

Provides :class:`AgentExecutor` which invokes Replicate models and
streams results back as an async iterator.

Features:
    - Concurrency limiter (semaphore) to cap parallel calls
    - Automatic retry with decorrelated jitter backoff
    - Model catalogue hydration from the Replicate API
    - Streaming and non-streaming output paths
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import anyio

from replicate_mcp.exceptions import (
    ExecutionError,
    ModelNotFoundError,
    TokenNotSetError,
)

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
# Model catalogue (hydrate from Replicate API)
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    """Cached metadata about a Replicate model."""

    owner: str
    name: str
    description: str = ""
    default_input_schema: dict[str, Any] = field(default_factory=dict)
    supports_streaming: bool = False


class ModelCatalogue:
    """In-memory cache of Replicate model metadata.

    Call :meth:`discover` to populate from the API.  Falls back to
    ``DEFAULT_MODEL_MAP`` if the API is unavailable.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._ttl_seconds: float = 300.0
        self._last_refresh: float = 0.0

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

        Returns the number of models cached.  This method is safe to
        call repeatedly — it respects the TTL.
        """
        if not self.is_stale():
            return len(self._models)

        try:
            import replicate as _replicate  # noqa: S603

            client = _replicate.Client(api_token=api_token)
            # client.models.list() returns a paginated iterator
            count = 0
            for model in client.models.list():
                key = f"{model.owner}/{model.name}"
                self._models[key] = ModelInfo(
                    owner=model.owner,
                    name=model.name,
                    description=getattr(model, "description", "") or "",
                )
                count += 1
                if count >= limit:
                    break
            self._last_refresh = time.monotonic()
            return count
        except Exception:  # noqa: BLE001
            logger.warning("Model catalogue discovery failed; using static map")
            return 0


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------


def _decorrelated_jitter(
    base: float = 0.5,
    cap: float = 30.0,
    attempt: int = 0,
) -> float:
    """Decorrelated jitter backoff.

    Returns a sleep duration in seconds.
    """
    sleep = min(cap, base * (2 ** attempt))
    return random.uniform(0, sleep)  # noqa: S311


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class AgentExecutor:
    """Execute Replicate model calls with streaming, concurrency, and retry.

    Usage::

        executor = AgentExecutor(max_concurrency=5, max_retries=3)
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
    ) -> None:
        self._model_map = model_map or dict(DEFAULT_MODEL_MAP)
        self._api_token = api_token or os.environ.get("REPLICATE_API_TOKEN", "")
        self._semaphore = anyio.Semaphore(max_concurrency)
        self._max_retries = max_retries
        self._retry_base = retry_base
        self._catalogue = catalogue or ModelCatalogue()

    @property
    def catalogue(self) -> ModelCatalogue:
        return self._catalogue

    def resolve_model(self, agent_id: str) -> str:
        """Map a short agent name to a full Replicate model identifier.

        Checks the static model map first, then the live catalogue.
        Returns *agent_id* unchanged if it already contains ``/``.

        Raises:
            ModelNotFoundError: If the identifier cannot be resolved.
        """
        if "/" in agent_id:
            return agent_id
        if agent_id in self._model_map:
            return self._model_map[agent_id]
        # Check catalogue
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

        Applies a concurrency limiter and retries on transient errors.

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
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            start = time.monotonic()
            try:
                async with self._semaphore:
                    async for chunk in self._invoke(agent_id, model_id, payload, start):
                        yield chunk
                return  # success

            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self._max_retries:
                    delay = _decorrelated_jitter(self._retry_base, 30.0, attempt)
                    logger.warning(
                        "Retry %d/%d for %s after %.1fs: %s",
                        attempt + 1,
                        self._max_retries,
                        model_id,
                        delay,
                        exc,
                    )
                    await anyio.sleep(delay)

        # All retries exhausted
        elapsed_ms = 0.0
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

            if hasattr(output, "__iter__") and not isinstance(output, (str, bytes)):
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
