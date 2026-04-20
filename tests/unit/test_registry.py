"""Unit tests for the agent registry (v2)."""

from __future__ import annotations

import pytest

from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry
from replicate_mcp.exceptions import AgentNotFoundError, DuplicateAgentError


def _make_agent(name: str = "test-agent", **kw) -> AgentMetadata:
    """Helper to create an AgentMetadata instance."""
    defaults = dict(
        safe_name=name,
        description=f"Test agent {name}",
        input_schema={"type": "object"},
        supports_streaming=True,
    )
    defaults.update(kw)
    return AgentMetadata(**defaults)


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

    def test_replicate_model_with_model_set(self) -> None:
        meta = _make_agent("chat", model="meta/llama-3")
        assert meta.replicate_model() == "meta/llama-3"

    def test_replicate_model_fallback_to_safe_name(self) -> None:
        meta = _make_agent("chat")
        assert meta.replicate_model() == "chat"

    def test_tags_default_empty(self) -> None:
        meta = _make_agent("x")
        assert meta.tags == []

    def test_tags_independent_between_instances(self) -> None:
        a = _make_agent("a")
        b = _make_agent("b")
        a.tags.append("modified")
        assert "modified" not in b.tags


class TestAgentRegistry:
    """Tests for AgentRegistry (v2 dict-backed)."""

    def test_empty_registry(self) -> None:
        reg = AgentRegistry()
        assert reg.count == 0
        assert list(reg.get_available_models()) == []
        assert reg.list_agents() == {}

    def test_register_single_agent(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("alpha"))
        assert reg.count == 1
        assert reg.has("alpha")
        assert reg.get("alpha").safe_name == "alpha"

    def test_register_duplicate_raises(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("dup"))
        with pytest.raises(DuplicateAgentError, match="dup"):
            reg.register(_make_agent("dup"))

    def test_register_or_update_overwrites(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("x", description="v1"))
        reg.register_or_update(_make_agent("x", description="v2"))
        assert reg.get("x").description == "v2"
        assert reg.count == 1

    def test_get_missing_raises(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(AgentNotFoundError, match="missing"):
            reg.get("missing")

    def test_remove(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("rm"))
        removed = reg.remove("rm")
        assert removed.safe_name == "rm"
        assert not reg.has("rm")
        assert reg.count == 0

    def test_remove_missing_raises(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(AgentNotFoundError):
            reg.remove("ghost")

    def test_register_multiple_agents(self) -> None:
        reg = AgentRegistry()
        for name in ["a", "b", "c"]:
            reg.register(_make_agent(name))
        assert reg.count == 3
        assert set(reg.list_agents().keys()) == {"a", "b", "c"}

    def test_list_agents_returns_copy(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("x"))
        copy = reg.list_agents()
        copy.clear()
        assert reg.count == 1  # internal state unchanged

    def test_get_available_models_returns_all(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("x"))
        first = list(reg.get_available_models())
        second = list(reg.get_available_models())
        assert len(first) == len(second) == 1

    def test_filter_by_tag(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("a", tags=["text", "chat"]))
        reg.register(_make_agent("b", tags=["image"]))
        reg.register(_make_agent("c", tags=["text"]))
        text_agents = reg.filter_by_tag("text")
        assert len(text_agents) == 2
        assert {a.safe_name for a in text_agents} == {"a", "c"}

    def test_clear(self) -> None:
        reg = AgentRegistry()
        reg.register(_make_agent("a"))
        reg.register(_make_agent("b"))
        reg.clear()
        assert reg.count == 0