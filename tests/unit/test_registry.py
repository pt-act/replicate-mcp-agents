"""Unit tests for the agent registry."""

from __future__ import annotations

from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry


def _make_agent(name: str = "test-agent") -> AgentMetadata:
    """Helper to create an AgentMetadata instance."""
    return AgentMetadata(
        safe_name=name,
        description=f"Test agent {name}",
        input_schema={"type": "object"},
        supports_streaming=True,
    )


class TestAgentMetadata:
    """Tests for AgentMetadata dataclass."""

    def test_required_fields(self) -> None:
        meta = _make_agent("my-agent")
        assert meta.safe_name == "my-agent"
        assert meta.supports_streaming is True
        assert meta.estimated_cost is None
        assert meta.avg_latency_ms is None

    def test_optional_fields(self) -> None:
        meta = AgentMetadata(
            safe_name="priced",
            description="Costly agent",
            input_schema={},
            supports_streaming=False,
            estimated_cost=0.05,
            avg_latency_ms=2000,
        )
        assert meta.estimated_cost == 0.05
        assert meta.avg_latency_ms == 2000


class TestAgentRegistry:
    """Tests for AgentRegistry."""

    def test_empty_registry(self) -> None:
        reg = AgentRegistry()
        assert list(reg.get_available_models()) == []

    def test_register_single_agent(self) -> None:
        reg = AgentRegistry()
        agent = _make_agent("alpha")
        reg.register(agent)
        models = list(reg.get_available_models())
        assert len(models) == 1
        assert models[0].safe_name == "alpha"

    def test_register_multiple_agents(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("a"))
        reg.register(_make_agent("b"))
        reg.register(_make_agent("c"))
        models = list(reg.get_available_models())
        assert len(models) == 3
        names = {m.safe_name for m in models}
        assert names == {"a", "b", "c"}

    def test_get_available_models_returns_all(self) -> None:
        """Ensure we can iterate multiple times."""
        reg = AgentRegistry()
        reg.register(_make_agent("x"))
        first = list(reg.get_available_models())
        second = list(reg.get_available_models())
        assert len(first) == len(second) == 1

    def test_register_preserves_order(self) -> None:
        reg = AgentRegistry()
        for name in ["z", "a", "m"]:
            reg.register(_make_agent(name))
        names = [m.safe_name for m in reg.get_available_models()]
        assert names == ["z", "a", "m"]