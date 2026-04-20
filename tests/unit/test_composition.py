"""Unit tests for the workflow composition primitives."""

from __future__ import annotations

import pytest

from replicate_mcp.agents.composition import AgentNode, AgentWorkflow, WorkflowEdge


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


class TestAgentWorkflow:
    """Tests for AgentWorkflow."""

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

    def test_add_edge_returns_self(self) -> None:
        wf = AgentWorkflow(name="w", description="d")
        result = wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        assert result is wf

    def test_add_edge(self) -> None:
        wf = AgentWorkflow(name="w", description="d")
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        assert len(wf.edges) == 1
        assert wf.edges[0].from_agent == "a"

    def test_chain_fluent_api(self) -> None:
        """Verify the fluent (builder) pattern works."""
        wf = (
            AgentWorkflow(name="pipeline", description="pipe")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
            .add_edge(WorkflowEdge(from_agent="a", to_agent="b"))
        )
        assert len(wf.nodes) == 2
        assert len(wf.edges) == 1

    @pytest.mark.asyncio()
    async def test_execute_yields_initial_input(self) -> None:
        wf = AgentWorkflow(name="demo", description="d")
        results = [chunk async for chunk in wf.execute({"key": "value"})]
        assert len(results) == 1
        assert results[0]["workflow"] == "demo"
        assert results[0]["input"] == {"key": "value"}

    def test_nodes_default_independent(self) -> None:
        """Ensure default_factory gives each workflow its own dict."""
        w1 = AgentWorkflow(name="a", description="a")
        w2 = AgentWorkflow(name="b", description="b")
        w1.add_agent("x", AgentNode(model_id="m/x", role="r"))
        assert "x" not in w2.nodes