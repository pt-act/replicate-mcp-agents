"""Workflow composition primitives for Replicate MCP agents."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

TransformFn = Callable[[dict[str, Any]], dict[str, Any]]
ConditionFn = Callable[[dict[str, Any]], bool]


@dataclass
class AgentNode:
    """Individual agent definition in an orchestration graph."""

    model_id: str
    role: str
    streaming: bool = False
    fallback_model: str | None = None


@dataclass
class WorkflowEdge:
    """Represents data flow between two agents in the graph."""

    from_agent: str
    to_agent: str
    transform: TransformFn | None = None
    condition: ConditionFn | None = None


@dataclass
class AgentWorkflow:
    """Declarative multi-agent workflow with checkpoint support."""

    name: str
    description: str
    nodes: dict[str, AgentNode] = field(default_factory=dict)
    edges: list[WorkflowEdge] = field(default_factory=list)

    def add_agent(self, agent_id: str, node: AgentNode) -> AgentWorkflow:
        self.nodes[agent_id] = node
        return self

    def add_edge(self, edge: WorkflowEdge) -> AgentWorkflow:
        self.edges.append(edge)
        return self

    async def execute(
        self,
        initial_input: dict[str, Any],
        resume_from: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Placeholder execution pipeline.

        Until the full workflow engine is implemented, this coroutine simply
        yields the initial input to demonstrate streaming behaviour.
        """

        _ = resume_from
        yield {"workflow": self.name, "input": initial_input}


__all__ = ["AgentNode", "WorkflowEdge", "AgentWorkflow"]
