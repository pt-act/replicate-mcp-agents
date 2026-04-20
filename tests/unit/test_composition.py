"""Unit tests for the workflow composition primitives.

Covers:
    - AgentNode / WorkflowEdge dataclasses
    - detect_cycle (DFS 3-colour)
    - topological_sort (Kahn's algorithm)
    - AgentWorkflow: add/remove, cycle rejection, levels, execution
"""

from __future__ import annotations

import pytest

from replicate_mcp.agents.composition import (
    AgentNode,
    AgentWorkflow,
    WorkflowEdge,
    detect_cycle,
    topological_sort,
)
from replicate_mcp.exceptions import (
    CycleDetectedError,
    NodeNotFoundError,
    WorkflowValidationError,
)


# -----------------------------------------------------------------------
# AgentNode
# -----------------------------------------------------------------------


class TestAgentNode:
    """Tests for AgentNode dataclass."""

    def test_required_fields(self) -> None:
        node = AgentNode(model_id="meta/llama-3", role="orchestrator")
        assert node.model_id == "meta/llama-3"
        assert node.role == "orchestrator"
        assert node.streaming is False
        assert node.fallback_model is None

    def test_optional_fields(self) -> None:
        node = AgentNode(
            model_id="openai/gpt-4",
            role="analyst",
            streaming=True,
            fallback_model="anthropic/claude-3",
        )
        assert node.streaming is True
        assert node.fallback_model == "anthropic/claude-3"


# -----------------------------------------------------------------------
# WorkflowEdge
# -----------------------------------------------------------------------


class TestWorkflowEdge:
    """Tests for WorkflowEdge dataclass."""

    def test_basic_edge(self) -> None:
        edge = WorkflowEdge(from_agent="a", to_agent="b")
        assert edge.from_agent == "a"
        assert edge.to_agent == "b"
        assert edge.transform is None
        assert edge.condition is None

    def test_edge_with_transform(self) -> None:
        fn = lambda d: {"prompt": d["text"]}  # noqa: E731
        edge = WorkflowEdge(from_agent="a", to_agent="b", transform=fn)
        result = edge.transform({"text": "hello"})  # type: ignore[misc]
        assert result == {"prompt": "hello"}

    def test_edge_with_condition(self) -> None:
        cond = lambda d: d.get("score", 0) > 0.5  # noqa: E731
        edge = WorkflowEdge(from_agent="a", to_agent="b", condition=cond)
        assert edge.condition({"score": 0.8}) is True  # type: ignore[misc]
        assert edge.condition({"score": 0.2}) is False  # type: ignore[misc]


# -----------------------------------------------------------------------
# detect_cycle (standalone function)
# -----------------------------------------------------------------------


class TestDetectCycle:
    """Tests for the DFS 3-colour cycle detection."""

    def test_no_cycle_linear(self) -> None:
        assert detect_cycle({"a", "b", "c"}, {"a": ["b"], "b": ["c"]}) is None

    def test_simple_cycle(self) -> None:
        result = detect_cycle({"a", "b"}, {"a": ["b"], "b": ["a"]})
        assert result is not None
        assert len(result) >= 2

    def test_triangle_cycle(self) -> None:
        adj = {"a": ["b"], "b": ["c"], "c": ["a"]}
        result = detect_cycle({"a", "b", "c"}, adj)
        assert result is not None

    def test_self_loop(self) -> None:
        result = detect_cycle({"a"}, {"a": ["a"]})
        assert result is not None

    def test_disconnected_no_cycle(self) -> None:
        assert detect_cycle({"a", "b", "c"}, {"a": ["b"]}) is None

    def test_empty_graph(self) -> None:
        assert detect_cycle(set(), {}) is None

    def test_single_node_no_edges(self) -> None:
        assert detect_cycle({"a"}, {}) is None

    def test_diamond_no_cycle(self) -> None:
        adj = {"a": ["b", "c"], "b": ["d"], "c": ["d"]}
        assert detect_cycle({"a", "b", "c", "d"}, adj) is None


# -----------------------------------------------------------------------
# topological_sort (standalone function)
# -----------------------------------------------------------------------


class TestTopologicalSort:
    """Tests for Kahn's algorithm topological sort."""

    def test_linear_chain(self) -> None:
        levels = topological_sort({"a", "b", "c"}, {"a": ["b"], "b": ["c"]})
        assert levels == [["a"], ["b"], ["c"]]

    def test_parallel_roots(self) -> None:
        levels = topological_sort({"a", "b", "c"}, {"a": ["c"], "b": ["c"]})
        assert levels[0] == ["a", "b"]  # sorted within level
        assert levels[1] == ["c"]

    def test_diamond(self) -> None:
        adj = {"a": ["b", "c"], "b": ["d"], "c": ["d"]}
        levels = topological_sort({"a", "b", "c", "d"}, adj)
        assert levels[0] == ["a"]
        assert levels[1] == ["b", "c"]
        assert levels[2] == ["d"]

    def test_single_node(self) -> None:
        assert topological_sort({"a"}, {}) == [["a"]]

    def test_no_edges(self) -> None:
        levels = topological_sort({"a", "b", "c"}, {})
        assert levels == [["a", "b", "c"]]

    def test_cycle_raises(self) -> None:
        with pytest.raises(CycleDetectedError):
            topological_sort({"a", "b"}, {"a": ["b"], "b": ["a"]})

    def test_complex_dag(self) -> None:
        """A -> B -> D, A -> C -> D, C -> E"""
        adj = {"a": ["b", "c"], "b": ["d"], "c": ["d", "e"]}
        levels = topological_sort({"a", "b", "c", "d", "e"}, adj)
        flat = [n for l in levels for n in l]
        # Verify ordering constraints
        assert flat.index("a") < flat.index("b")
        assert flat.index("a") < flat.index("c")
        assert flat.index("b") < flat.index("d")
        assert flat.index("c") < flat.index("d")
        assert flat.index("c") < flat.index("e")


# -----------------------------------------------------------------------
# AgentWorkflow
# -----------------------------------------------------------------------


class TestAgentWorkflow:
    """Tests for AgentWorkflow DAG operations."""

    def test_empty_workflow(self) -> None:
        wf = AgentWorkflow(name="test", description="A test workflow")
        assert wf.name == "test"
        assert wf.nodes == {}
        assert wf.edges == []

    def test_add_agent_returns_self(self) -> None:
        wf = AgentWorkflow(name="w", description="d")
        result = wf.add_agent("a", AgentNode(model_id="m/a", role="r"))
        assert result is wf

    def test_add_agent(self) -> None:
        wf = AgentWorkflow(name="w", description="d")
        wf.add_agent("alpha", AgentNode(model_id="m/alpha", role="orchestrator"))
        assert "alpha" in wf.nodes
        assert wf.nodes["alpha"].model_id == "m/alpha"

    def test_add_edge_validates_nodes_exist(self) -> None:
        wf = AgentWorkflow(name="w", description="d")
        wf.add_agent("a", AgentNode(model_id="m/a", role="r"))
        with pytest.raises(NodeNotFoundError):
            wf.add_edge(WorkflowEdge(from_agent="a", to_agent="missing"))

    def test_add_edge_rejects_cycle(self) -> None:
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        with pytest.raises(CycleDetectedError):
            wf.add_edge(WorkflowEdge(from_agent="b", to_agent="a"))

    def test_add_edge_rejects_triangle_cycle(self) -> None:
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
            .add_agent("c", AgentNode(model_id="m/c", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        wf.add_edge(WorkflowEdge(from_agent="b", to_agent="c"))
        with pytest.raises(CycleDetectedError):
            wf.add_edge(WorkflowEdge(from_agent="c", to_agent="a"))

    def test_add_edge_rollback_on_cycle(self) -> None:
        """After a rejected cycle, the graph should remain unchanged."""
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        with pytest.raises(CycleDetectedError):
            wf.add_edge(WorkflowEdge(from_agent="b", to_agent="a"))
        # Only the valid edge should remain
        assert len(wf.edges) == 1
        assert wf.successors("a") == ["b"]
        assert wf.successors("b") == []

    def test_remove_agent(self) -> None:
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        wf.remove_agent("a")
        assert "a" not in wf.nodes
        assert len(wf.edges) == 0

    def test_remove_missing_agent_raises(self) -> None:
        wf = AgentWorkflow(name="w", description="d")
        with pytest.raises(NodeNotFoundError):
            wf.remove_agent("nonexistent")

    def test_predecessors_and_successors(self) -> None:
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
            .add_agent("c", AgentNode(model_id="m/c", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="c"))
        assert wf.successors("a") == ["b", "c"]
        assert wf.predecessors("b") == ["a"]
        assert wf.predecessors("a") == []

    def test_get_edge(self) -> None:
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        assert wf.get_edge("a", "b") is not None
        assert wf.get_edge("b", "a") is None

    def test_topological_order(self) -> None:
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
            .add_agent("c", AgentNode(model_id="m/c", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        wf.add_edge(WorkflowEdge(from_agent="b", to_agent="c"))
        order = wf.topological_order()
        assert order == ["a", "b", "c"]

    def test_execution_levels_diamond(self) -> None:
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
            .add_agent("c", AgentNode(model_id="m/c", role="r"))
            .add_agent("d", AgentNode(model_id="m/d", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="c"))
        wf.add_edge(WorkflowEdge(from_agent="b", to_agent="d"))
        wf.add_edge(WorkflowEdge(from_agent="c", to_agent="d"))
        levels = wf.execution_levels()
        assert levels == [["a"], ["b", "c"], ["d"]]

    def test_validate_ok(self) -> None:
        wf = (
            AgentWorkflow(name="w", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
        )
        assert wf.validate() == []

    def test_validate_empty(self) -> None:
        wf = AgentWorkflow(name="w", description="d")
        issues = wf.validate()
        assert any("no nodes" in i for i in issues)

    def test_chain_fluent_api(self) -> None:
        """Verify the fluent (builder) pattern works."""
        wf = (
            AgentWorkflow(name="pipeline", description="pipe")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        assert len(wf.nodes) == 2
        assert len(wf.edges) == 1

    def test_nodes_default_independent(self) -> None:
        """Ensure default_factory gives each workflow its own dict."""
        w1 = AgentWorkflow(name="a", description="a")
        w2 = AgentWorkflow(name="b", description="b")
        w1.add_agent("x", AgentNode(model_id="m/x", role="r"))
        assert "x" not in w2.nodes


# -----------------------------------------------------------------------
# Workflow execution
# -----------------------------------------------------------------------


class TestAgentWorkflowExecution:
    """Tests for the async execute() method."""

    @pytest.mark.asyncio()
    async def test_single_node_passthrough(self) -> None:
        wf = (
            AgentWorkflow(name="demo", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
        )
        results = [chunk async for chunk in wf.execute({"key": "value"})]
        assert len(results) == 1
        assert results[0]["node"] == "a"
        assert results[0]["output"]["passthrough"] is True
        assert results[0]["done"] is True

    @pytest.mark.asyncio()
    async def test_linear_chain_passthrough(self) -> None:
        wf = (
            AgentWorkflow(name="chain", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        results = [chunk async for chunk in wf.execute({"seed": 42})]
        assert len(results) == 2
        assert results[0]["node"] == "a"
        assert results[1]["node"] == "b"

    @pytest.mark.asyncio()
    async def test_fan_out_parallel(self) -> None:
        """Nodes at the same level should all execute."""
        wf = (
            AgentWorkflow(name="fan", description="d")
            .add_agent("root", AgentNode(model_id="m/r", role="r"))
            .add_agent("b1", AgentNode(model_id="m/b1", role="r"))
            .add_agent("b2", AgentNode(model_id="m/b2", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="root", to_agent="b1"))
        wf.add_edge(WorkflowEdge(from_agent="root", to_agent="b2"))
        results = [chunk async for chunk in wf.execute({"x": 1})]
        assert len(results) == 3
        level_1_nodes = {r["node"] for r in results if r["level"] == 1}
        assert level_1_nodes == {"b1", "b2"}

    @pytest.mark.asyncio()
    async def test_transform_applied_on_edge(self) -> None:
        wf = (
            AgentWorkflow(name="tx", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(
            from_agent="a",
            to_agent="b",
            transform=lambda d: {"transformed": True},
        ))
        results = [chunk async for chunk in wf.execute({"orig": 1})]
        # Node b's input should have been transformed
        b_result = [r for r in results if r["node"] == "b"][0]
        assert b_result["output"]["output"] == {"transformed": True}

    @pytest.mark.asyncio()
    async def test_condition_gates_edge(self) -> None:
        wf = (
            AgentWorkflow(name="cond", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(
            from_agent="a",
            to_agent="b",
            condition=lambda d: False,  # always block
        ))
        results = [chunk async for chunk in wf.execute({"x": 1})]
        # b should still execute, but with empty input (condition blocked data flow)
        b_result = [r for r in results if r["node"] == "b"][0]
        assert b_result["output"]["output"] == {}

    @pytest.mark.asyncio()
    async def test_empty_workflow_raises(self) -> None:
        wf = AgentWorkflow(name="empty", description="d")
        with pytest.raises(WorkflowValidationError):
            async for _ in wf.execute({}):
                pass

    @pytest.mark.asyncio()
    async def test_checkpoint_integration(self, tmp_path) -> None:
        wf = (
            AgentWorkflow(name="ckpt", description="d")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
        )
        results = [
            chunk async for chunk in wf.execute(
                {"v": 1},
                checkpoint_dir=tmp_path / "ckpts",
            )
        ]
        assert len(results) == 1
        # Verify checkpoint file was created
        ckpt_file = tmp_path / "ckpts" / "wf-ckpt.json"
        assert ckpt_file.exists()