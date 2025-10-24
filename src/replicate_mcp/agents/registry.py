"""Agent registry for discovering Replicate models."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class AgentMetadata:
    """Describes a Replicate-backed agent exposed via MCP."""

    safe_name: str
    description: str
    input_schema: dict
    supports_streaming: bool
    estimated_cost: float | None = None
    avg_latency_ms: int | None = None


class AgentRegistry:
    """Registry of available Replicate-powered agents."""

    def __init__(self) -> None:
        self._agents: list[AgentMetadata] = []

    def register(self, agent: AgentMetadata) -> None:
        """Register an agent with the in-memory catalogue."""

        self._agents.append(agent)

    def get_available_models(self) -> Iterable[AgentMetadata]:
        """Yield all registered agents.

        The real implementation will hydrate this list from Replicate's
        model catalogue; for now we return whatever has been locally
        registered.
        """

        yield from list(self._agents)


__all__ = ["AgentMetadata", "AgentRegistry"]
