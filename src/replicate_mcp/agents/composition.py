"""Workflow composition primitives for Replicate MCP agents.

This module provides:

- :class:`AgentNode` — an individual agent (model) in the workflow
- :class:`WorkflowEdge` — data-flow edge with optional transform/condition
- :class:`AgentWorkflow` — DAG-based multi-agent workflow with:
    - Kahn's algorithm topological sort
    - DFS 3-colour cycle detection on ``add_edge``
    - Parallel fan-out via :mod:`anyio` task groups
    - Per-node checkpoint save/resume
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from replicate_mcp.exceptions import (
    CycleDetectedError,
    NodeNotFoundError,
    WorkflowValidationError,
)
from replicate_mcp.utils.checkpointing import CheckpointManager

logger = logging.getLogger(__name__)

TransformFn = Callable[[dict[str, Any]], dict[str, Any]]
ConditionFn = Callable[[dict[str, Any]], bool]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DAG helpers (pure functions, easy to test)
# ---------------------------------------------------------------------------


def detect_cycle(
    nodes: set[str],
    adjacency: dict[str, list[str]],
) -> list[str] | None:
    """DFS 3-colour cycle detection.

    Returns a list of node IDs forming the cycle if one exists,
    otherwise ``None``.

    Colours:
        - WHITE (0) — unvisited
        - GREY  (1) — on the current DFS stack
        - BLACK (2) — fully explored
    """

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in nodes}
    parent: dict[str, str | None] = {n: None for n in nodes}

    def _dfs(u: str) -> list[str] | None:
        colour[u] = GREY
        for v in adjacency.get(u, []):
            if colour[v] == GREY:
                # Back edge → reconstruct cycle
                cycle = [v, u]
                cur = u
                while cur != v:
                    cur = parent[cur]  # type: ignore[assignment]
                    if cur is None:
                        break
                    cycle.append(cur)
                cycle.reverse()
                return cycle
            if colour[v] == WHITE:
                parent[v] = u
                result = _dfs(v)
                if result is not None:
                    return result
        colour[u] = BLACK
        return None

    for node in nodes:
        if colour[node] == WHITE:
            result = _dfs(node)
            if result is not None:
                return result
    return None


def topological_sort(
    nodes: set[str],
    adjacency: dict[str, list[str]],
) -> list[list[str]]:
    """Kahn's algorithm — returns *levels* (list of lists).

    Each level contains nodes that can run concurrently.  Levels are
    ordered so that all predecessors of nodes in level *i* appear in
    levels *< i*.

    Raises :class:`CycleDetectedError` if the graph has a cycle.
    """

    in_degree: dict[str, int] = {n: 0 for n in nodes}
    for u in nodes:
        for v in adjacency.get(u, []):
            in_degree[v] = in_degree.get(v, 0) + 1

    queue: deque[str] = deque(n for n in nodes if in_degree[n] == 0)
    levels: list[list[str]] = []
    visited = 0

    while queue:
        level: list[str] = []
        for _ in range(len(queue)):
            u = queue.popleft()
            level.append(u)
            visited += 1
            for v in adjacency.get(u, []):
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
        levels.append(sorted(level))  # deterministic ordering within level

    if visited != len(nodes):
        # Must have a cycle — find it for the error message
        cycle = detect_cycle(nodes, adjacency)
        raise CycleDetectedError(cycle or ["<unknown>"])

    return levels


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


@dataclass
class AgentWorkflow:
    """Declarative multi-agent workflow backed by a validated DAG.

    Features:
        - Cycle detection on every ``add_edge`` call
        - ``topological_order()`` for execution scheduling
        - ``execution_levels()`` to identify parallelisable groups
        - Checkpoint-integrated ``execute()`` with fan-out
    """

    name: str
    description: str
    nodes: dict[str, AgentNode] = field(default_factory=dict)
    edges: list[WorkflowEdge] = field(default_factory=list)

    # Private adjacency cache — rebuilt on mutation
    _adjacency: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list),
        init=False,
        repr=False,
    )

    # ---- mutation ----

    def add_agent(self, agent_id: str, node: AgentNode) -> AgentWorkflow:
        """Register a node in the workflow graph."""
        self.nodes[agent_id] = node
        # Ensure adjacency has an entry
        if agent_id not in self._adjacency:
            self._adjacency[agent_id] = []
        return self

    def add_edge(self, edge: WorkflowEdge) -> AgentWorkflow:
        """Add an edge, rejecting it if it would create a cycle.

        Raises:
            NodeNotFoundError: If either endpoint is not a registered node.
            CycleDetectedError: If adding this edge creates a cycle.
        """

        if edge.from_agent not in self.nodes:
            raise NodeNotFoundError(edge.from_agent)
        if edge.to_agent not in self.nodes:
            raise NodeNotFoundError(edge.to_agent)

        # Tentatively add and check for cycles
        self._adjacency[edge.from_agent].append(edge.to_agent)
        cycle = detect_cycle(set(self.nodes), dict(self._adjacency))
        if cycle is not None:
            # Roll back
            self._adjacency[edge.from_agent].pop()
            raise CycleDetectedError(cycle)

        self.edges.append(edge)
        return self

    def remove_agent(self, agent_id: str) -> AgentWorkflow:
        """Remove a node and all edges referencing it.

        Raises:
            NodeNotFoundError: If the node does not exist.
        """

        if agent_id not in self.nodes:
            raise NodeNotFoundError(agent_id)

        del self.nodes[agent_id]
        self.edges = [
            e for e in self.edges
            if e.from_agent != agent_id and e.to_agent != agent_id
        ]
        self._rebuild_adjacency()
        return self

    # ---- query ----

    def predecessors(self, node_id: str) -> list[str]:
        """Return node IDs that have an edge **to** *node_id*."""
        return [e.from_agent for e in self.edges if e.to_agent == node_id]

    def successors(self, node_id: str) -> list[str]:
        """Return node IDs that *node_id* has an edge **to**."""
        return list(self._adjacency.get(node_id, []))

    def get_edge(self, from_agent: str, to_agent: str) -> WorkflowEdge | None:
        """Return the edge between two nodes, or ``None``."""
        for e in self.edges:
            if e.from_agent == from_agent and e.to_agent == to_agent:
                return e
        return None

    def topological_order(self) -> list[str]:
        """Return a flat topological ordering of all nodes."""
        levels = self.execution_levels()
        return [node for level in levels for node in level]

    def execution_levels(self) -> list[list[str]]:
        """Return groups of nodes that can be executed in parallel.

        Each group (level) depends only on nodes in prior groups.
        """

        return topological_sort(set(self.nodes), dict(self._adjacency))

    def validate(self) -> list[str]:
        """Run structural validation, returning a list of issues (empty = ok).

        Checks:
            - No dangling edge endpoints
            - No cycles (should be impossible if ``add_edge`` was used)
            - At least one node
        """

        issues: list[str] = []
        if not self.nodes:
            issues.append("Workflow has no nodes")

        for edge in self.edges:
            if edge.from_agent not in self.nodes:
                issues.append(f"Edge references unknown source '{edge.from_agent}'")
            if edge.to_agent not in self.nodes:
                issues.append(f"Edge references unknown target '{edge.to_agent}'")

        cycle = detect_cycle(set(self.nodes), dict(self._adjacency))
        if cycle is not None:
            issues.append(f"Cycle detected: {' → '.join(cycle)}")

        return issues

    # ---- execution ----

    async def execute(
        self,
        initial_input: dict[str, Any],
        *,
        executor: Any | None = None,
        checkpoint_dir: Path | None = None,
        resume_from: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute the workflow DAG level-by-level.

        For each level, all nodes in the level are launched concurrently
        via :mod:`anyio`.  Edges' transform/condition functions are
        applied between levels.

        Args:
            initial_input: Seed data fed to root nodes.
            executor: An ``AgentExecutor`` (or compatible) instance.
                      If ``None``, nodes just pass through input data.
            checkpoint_dir: If set, per-node results are checkpointed.
            resume_from: Node ID to resume from (skip prior nodes).

        Yields:
            Dicts with keys ``node``, ``level``, ``output``, ``done``.
        """

        import anyio

        issues = self.validate()
        if issues:
            raise WorkflowValidationError("; ".join(issues))

        ckpt = CheckpointManager(base_path=checkpoint_dir) if checkpoint_dir else None
        session = f"wf-{self.name}"

        # Load existing checkpoint data if resuming
        node_outputs: dict[str, dict[str, Any]] = {}
        if ckpt and resume_from:
            try:
                saved = ckpt.load(session)
                node_outputs = saved.get("node_outputs", {})
            except FileNotFoundError:
                pass

        levels = self.execution_levels()
        skip = resume_from is not None

        for level_idx, level in enumerate(levels):
            if skip:
                # Skip levels until we reach the resume node
                if resume_from in level:
                    skip = False
                else:
                    continue

            async def _run_node(node_id: str) -> dict[str, Any]:
                # Gather inputs from predecessors (or use initial_input for roots)
                preds = self.predecessors(node_id)
                if preds:
                    node_input: dict[str, Any] = {}
                    for pred in preds:
                        pred_output = node_outputs.get(pred, {})
                        edge = self.get_edge(pred, node_id)
                        if edge and edge.condition:
                            if not edge.condition(pred_output):
                                continue
                        if edge and edge.transform:
                            pred_output = edge.transform(pred_output)
                        node_input.update(pred_output)
                else:
                    node_input = dict(initial_input)

                # Execute
                if executor is not None:
                    result_chunks: list[dict[str, Any]] = []
                    async for chunk in executor.run(node_id, node_input):
                        result_chunks.append(chunk)
                    # Final chunk has the full output
                    final = result_chunks[-1] if result_chunks else {}
                    output = {"output": final.get("output", ""), **final}
                else:
                    # No executor → passthrough (useful for testing)
                    output = {"output": node_input, "passthrough": True}

                return output

            # Fan-out: run all nodes in this level concurrently
            level_results: dict[str, dict[str, Any]] = {}

            async def _capture(nid: str) -> None:
                level_results[nid] = await _run_node(nid)

            async with anyio.create_task_group() as tg:
                for nid in level:
                    tg.start_soon(_capture, nid)

            # Store outputs and yield events
            for nid in sorted(level_results):
                result = level_results[nid]
                node_outputs[nid] = result

                yield {
                    "workflow": self.name,
                    "node": nid,
                    "level": level_idx,
                    "output": result,
                    "done": level_idx == len(levels) - 1 and nid == sorted(level_results)[-1],
                }

            # Checkpoint after each level
            if ckpt:
                ckpt.save(session, {
                    "workflow": self.name,
                    "completed_level": level_idx,
                    "node_outputs": node_outputs,
                })

    # ---- internal helpers ----

    def _rebuild_adjacency(self) -> None:
        """Rebuild adjacency from edge list."""
        self._adjacency = defaultdict(list)
        for n in self.nodes:
            if n not in self._adjacency:
                self._adjacency[n] = []
        for e in self.edges:
            self._adjacency[e.from_agent].append(e.to_agent)


__all__ = [
    "AgentNode",
    "WorkflowEdge",
    "AgentWorkflow",
    "detect_cycle",
    "topological_sort",
]
