"""Agent execution helpers.

Provides :class:`AgentExecutor` which invokes Replicate models and
streams results back as an async iterator.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# Model ID mapping — maps short safe names to full Replicate model IDs.
# In Phase 2 this will be driven by the AgentRegistry / model catalogue.
DEFAULT_MODEL_MAP: dict[str, str] = {
    "llama3_chat": "meta/meta-llama-3-70b-instruct",
}


class ReplicateError(Exception):
    """Raised when a Replicate API call fails."""


class AgentExecutor:
    """Execute Replicate model calls with streaming support.

    The executor wraps the Replicate Python SDK to provide:
    - Synchronous and streaming model invocations
    - Telemetry-ready timing and cost metadata per call
    - Graceful error handling with structured error dicts

    Usage::

        executor = AgentExecutor()
        async for chunk in executor.run("llama3_chat", {"prompt": "Hi"}):
            print(chunk)
    """

    def __init__(
        self,
        *,
        model_map: dict[str, str] | None = None,
        api_token: str | None = None,
    ) -> None:
        self._model_map = model_map or dict(DEFAULT_MODEL_MAP)
        self._api_token = api_token or os.environ.get("REPLICATE_API_TOKEN", "")

    def resolve_model(self, agent_id: str) -> str:
        """Map a short agent name to a full Replicate model identifier.

        Returns the *agent_id* unchanged if it already looks like a
        Replicate model path (i.e. contains ``/``).
        """
        if "/" in agent_id:
            return agent_id
        if agent_id in self._model_map:
            return self._model_map[agent_id]
        raise KeyError(
            f"Agent '{agent_id}' not found in model map. "
            f"Available: {sorted(self._model_map)}"
        )

    async def run(
        self,
        agent_id: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Invoke a Replicate model and yield result chunks.

        Each yielded dict contains at minimum::

            {
                "agent": "<agent_id>",
                "chunk": "<text or data>",
                "done": False,
            }

        The final chunk has ``"done": True`` and includes timing metadata.

        If the ``REPLICATE_API_TOKEN`` env var is not set, yields an
        error dict instead of raising, so MCP clients receive a
        structured error message.
        """

        if not self._api_token:
            yield {
                "agent": agent_id,
                "error": "REPLICATE_API_TOKEN is not set",
                "done": True,
            }
            return

        model_id = self.resolve_model(agent_id)
        start = time.monotonic()

        try:
            import replicate  # noqa: S603 — lazy import to avoid hard dep at module level

            output = replicate.run(model_id, input=payload)

            # replicate.run() returns different types depending on the model:
            #   - str or list[str] for text models
            #   - list[FileOutput] for image models
            #   - an iterator for streaming text models
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

        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.exception("Replicate call failed for %s", model_id)
            yield {
                "agent": agent_id,
                "model": model_id,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round(elapsed_ms, 1),
                "done": True,
            }


__all__ = ["AgentExecutor", "ReplicateError", "DEFAULT_MODEL_MAP"]
