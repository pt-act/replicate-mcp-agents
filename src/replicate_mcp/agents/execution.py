"""Agent execution helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class AgentExecutor:
    """Placeholder executor that will orchestrate Replicate calls."""

    async def run(self, agent_id: str, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Stream dummy agent responses.

        TODO: integrate with Replicate streaming responses.
        """

        yield {"agent": agent_id, "payload": payload}


__all__ = ["AgentExecutor"]
