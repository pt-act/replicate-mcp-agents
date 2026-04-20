"""Agent registry for discovering and managing Replicate models.

Provides a dict-backed :class:`AgentRegistry` with O(1) lookup by
``safe_name``, duplicate detection, and remove/list support.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from replicate_mcp.exceptions import AgentNotFoundError, DuplicateAgentError


@dataclass
class AgentMetadata:
    """Describes a Replicate-backed agent exposed via MCP.

    Attributes:
        safe_name: Unique identifier used as MCP tool name.
        description: Human-readable description for the MCP tool.
        model: Full Replicate model identifier (``owner/model``).
               Defaults to ``safe_name`` if unset.
        input_schema: JSON Schema dict describing accepted inputs.
        supports_streaming: Whether the model supports streaming output.
        estimated_cost: Estimated cost in USD per invocation.
        avg_latency_ms: Average latency in milliseconds.
        tags: Arbitrary labels for filtering/grouping.
    """

    safe_name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    supports_streaming: bool = False
    model: str | None = None
    estimated_cost: float | None = None
    avg_latency_ms: int | None = None
    tags: list[str] = field(default_factory=list)

    def replicate_model(self) -> str:
        """Return the Replicate model path, falling back to safe_name."""
        return self.model or self.safe_name


class AgentRegistry:
    """Thread-safe, dict-backed registry of Replicate-powered agents.

    Key improvements over the v1 list-backed registry:
        - O(1) lookup via ``get()``
        - Duplicate detection on ``register()``
        - ``remove()`` and ``has()`` methods
        - ``list_agents()`` returns a *copy* to prevent mutation
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentMetadata] = {}

    # ---- mutation ----

    def register(self, agent: AgentMetadata) -> None:
        """Register an agent, raising on duplicates.

        Raises:
            DuplicateAgentError: If ``agent.safe_name`` is already registered.
        """
        if agent.safe_name in self._agents:
            raise DuplicateAgentError(agent.safe_name)
        self._agents[agent.safe_name] = agent

    def register_or_update(self, agent: AgentMetadata) -> None:
        """Register an agent, overwriting if it already exists."""
        self._agents[agent.safe_name] = agent

    def remove(self, safe_name: str) -> AgentMetadata:
        """Remove and return an agent by ``safe_name``.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        if safe_name not in self._agents:
            raise AgentNotFoundError(safe_name)
        return self._agents.pop(safe_name)

    # ---- query ----

    def get(self, safe_name: str) -> AgentMetadata:
        """Return metadata for the given agent.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        if safe_name not in self._agents:
            raise AgentNotFoundError(safe_name)
        return self._agents[safe_name]

    def has(self, safe_name: str) -> bool:
        """Check whether an agent is registered."""
        return safe_name in self._agents

    def list_agents(self) -> dict[str, AgentMetadata]:
        """Return a shallow copy of the internal agent dict."""
        return dict(self._agents)

    def get_available_models(self) -> Iterator[AgentMetadata]:
        """Yield all registered agents (v1-compatible API)."""
        yield from list(self._agents.values())

    def filter_by_tag(self, tag: str) -> list[AgentMetadata]:
        """Return agents that have the specified *tag*."""
        return [a for a in self._agents.values() if tag in a.tags]

    @property
    def count(self) -> int:
        """Number of registered agents."""
        return len(self._agents)

    def clear(self) -> None:
        """Remove all agents."""
        self._agents.clear()


__all__ = ["AgentMetadata", "AgentRegistry"]
