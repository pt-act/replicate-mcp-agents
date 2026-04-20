"""Tests for replicate_mcp.sdk — @agent decorator and fluent builders."""

from __future__ import annotations

import pytest

from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry
from replicate_mcp.exceptions import ReplicateMCPError
from replicate_mcp.sdk import (
    AgentBuilder,
    AgentContext,
    WorkflowBuilder,
    WorkflowSpec,
    WorkflowStep,
    agent,
    get_default_registry,
    reset_default_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry() -> None:
    """Isolate tests from each other by resetting the default registry."""
    reset_default_registry()
    yield
    reset_default_registry()


# ---------------------------------------------------------------------------
# @agent decorator — bare usage
# ---------------------------------------------------------------------------


class TestAgentDecoratorBare:
    def test_bare_decorator_registers(self) -> None:
        @agent
        def my_model(prompt: str) -> dict:
            """A simple chat model."""
            return {"prompt": prompt}

        reg = get_default_registry()
        assert reg.has("my_model")

    def test_bare_decorator_uses_docstring(self) -> None:
        @agent
        def summarise(text: str) -> dict:
            """Summarise the provided text."""
            return {}

        meta = get_default_registry().get("summarise")
        assert "Summarise" in meta.description

    def test_bare_decorator_preserves_callable(self) -> None:
        @agent
        def echo(x: int) -> int:
            return x * 2

        assert echo(3) == 6

    def test_bare_decorator_sets_model_to_function_name(self) -> None:
        @agent
        def my_agent() -> dict:
            return {}

        meta = get_default_registry().get("my_agent")
        assert meta.model == "my_agent" or meta.replicate_model() == "my_agent"

    def test_metadata_attached_to_wrapper(self) -> None:
        @agent
        def chatbot() -> dict:
            """Chat."""
            return {}

        assert hasattr(chatbot, "__agent_metadata__")
        assert isinstance(chatbot.__agent_metadata__, AgentMetadata)


# ---------------------------------------------------------------------------
# @agent decorator — with arguments
# ---------------------------------------------------------------------------


class TestAgentDecoratorWithArgs:
    def test_custom_model(self) -> None:
        @agent(model="meta/llama-3")
        def llama() -> dict:
            return {}

        meta = get_default_registry().get("llama")
        assert meta.model == "meta/llama-3"

    def test_custom_description(self) -> None:
        @agent(description="Custom desc")
        def my_func() -> dict:
            return {}

        meta = get_default_registry().get("my_func")
        assert meta.description == "Custom desc"

    def test_tags(self) -> None:
        @agent(tags=["chat", "llm"])
        def tagged() -> dict:
            return {}

        meta = get_default_registry().get("tagged")
        assert "chat" in meta.tags
        assert "llm" in meta.tags

    def test_streaming_flag(self) -> None:
        @agent(supports_streaming=True)
        def streamer() -> dict:
            return {}

        meta = get_default_registry().get("streamer")
        assert meta.supports_streaming is True

    def test_estimated_cost(self) -> None:
        @agent(estimated_cost=0.005)
        def costly() -> dict:
            return {}

        meta = get_default_registry().get("costly")
        assert meta.estimated_cost == pytest.approx(0.005)

    def test_custom_registry(self) -> None:
        custom_reg = AgentRegistry()

        @agent(registry=custom_reg)
        def isolated() -> dict:
            return {}

        assert custom_reg.has("isolated")
        assert not get_default_registry().has("isolated")

    def test_callable_preserved_with_args(self) -> None:
        @agent(model="test/model")
        def compute(x: int) -> int:
            return x + 1

        assert compute(5) == 6

    def test_input_schema_stored(self) -> None:
        schema = {"type": "object", "properties": {"prompt": {"type": "string"}}}

        @agent(input_schema=schema)
        def schema_agent() -> dict:
            return {}

        meta = get_default_registry().get("schema_agent")
        assert meta.input_schema == schema

    def test_overwrite_existing(self) -> None:
        @agent(description="v1")
        def versioned() -> dict:
            return {}

        @agent(description="v2")
        def versioned() -> dict:  # noqa: F811
            return {}

        # register_or_update should overwrite
        meta = get_default_registry().get("versioned")
        assert meta.description == "v2"


# ---------------------------------------------------------------------------
# AgentBuilder
# ---------------------------------------------------------------------------


class TestAgentBuilder:
    def test_build_minimal(self) -> None:
        meta = AgentBuilder("test").build()
        assert meta.safe_name == "test"

    def test_model_setter(self) -> None:
        meta = AgentBuilder("a").model("owner/name").build()
        assert meta.model == "owner/name"

    def test_description_setter(self) -> None:
        meta = AgentBuilder("a").description("hello").build()
        assert meta.description == "hello"

    def test_tag_appends(self) -> None:
        meta = AgentBuilder("a").tag("t1").tag("t2", "t3").build()
        assert "t1" in meta.tags
        assert "t2" in meta.tags
        assert "t3" in meta.tags

    def test_streaming(self) -> None:
        meta = AgentBuilder("a").streaming(True).build()
        assert meta.supports_streaming is True

    def test_estimated_cost(self) -> None:
        meta = AgentBuilder("a").estimated_cost(0.002).build()
        assert meta.estimated_cost == pytest.approx(0.002)

    def test_avg_latency(self) -> None:
        meta = AgentBuilder("a").avg_latency(3000).build()
        assert meta.avg_latency_ms == 3000

    def test_input_schema(self) -> None:
        schema = {"type": "object"}
        meta = AgentBuilder("a").input_schema(schema).build()
        assert meta.input_schema == schema

    def test_fluent_chaining(self) -> None:
        meta = (
            AgentBuilder("chain")
            .model("x/y")
            .description("chained")
            .tag("a")
            .streaming()
            .estimated_cost(0.01)
            .avg_latency(1000)
            .build()
        )
        assert meta.model == "x/y"
        assert meta.description == "chained"
        assert meta.supports_streaming is True

    def test_register_uses_default_registry(self) -> None:
        meta = AgentBuilder("registered_agent").register()
        assert get_default_registry().has("registered_agent")
        assert isinstance(meta, AgentMetadata)

    def test_register_uses_custom_registry(self) -> None:
        custom = AgentRegistry()
        AgentBuilder("custom_reg").register(custom)
        assert custom.has("custom_reg")

    def test_empty_safe_name_raises(self) -> None:
        with pytest.raises(ValueError):
            AgentBuilder("")


# ---------------------------------------------------------------------------
# WorkflowBuilder
# ---------------------------------------------------------------------------


class TestWorkflowBuilder:
    def test_single_step(self) -> None:
        spec = WorkflowBuilder("wf").then("agent_a").build()
        assert spec.step_count == 1
        assert spec.agent_names == ["agent_a"]

    def test_multi_step(self) -> None:
        spec = (
            WorkflowBuilder("pipeline")
            .then("step1")
            .then("step2")
            .then("step3")
            .build()
        )
        assert spec.step_count == 3
        assert spec.agent_names == ["step1", "step2", "step3"]

    def test_input_map(self) -> None:
        spec = (
            WorkflowBuilder("wf")
            .then("step1", input_map={"text": "raw_input"})
            .build()
        )
        assert spec.steps[0].input_map == {"text": "raw_input"}

    def test_condition(self) -> None:
        spec = (
            WorkflowBuilder("wf")
            .then("step1", condition="len(text) > 100")
            .build()
        )
        assert spec.steps[0].condition == "len(text) > 100"

    def test_description_stored(self) -> None:
        spec = WorkflowBuilder("wf").description("My pipeline").then("a").build()
        assert spec.description == "My pipeline"

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError):
            WorkflowBuilder("")

    def test_no_steps_raises(self) -> None:
        with pytest.raises(ReplicateMCPError):
            WorkflowBuilder("empty").build()

    def test_spec_name_and_description(self) -> None:
        spec = WorkflowBuilder("my-wf").then("a").build()
        assert spec.name == "my-wf"

    def test_workflow_spec_properties(self) -> None:
        steps = [WorkflowStep("a"), WorkflowStep("b")]
        spec = WorkflowSpec(name="wf", description="desc", steps=steps)
        assert spec.step_count == 2
        assert spec.agent_names == ["a", "b"]


# ---------------------------------------------------------------------------
# AgentContext
# ---------------------------------------------------------------------------


class TestAgentContext:
    def test_context_isolates_registry(self) -> None:
        outer = get_default_registry()  # save before entering context
        with AgentContext() as ctx:
            # Inside the context, _default_registry is ctx.registry
            @agent
            def ctx_agent() -> dict:
                return {}

            assert ctx.registry.has("ctx_agent")
            assert not outer.has("ctx_agent")  # outer registry unaffected

    def test_context_restores_default_registry(self) -> None:
        original = get_default_registry()
        with AgentContext():
            pass
        assert get_default_registry() is original

    def test_context_registry_isolated_from_default(self) -> None:
        @agent
        def outside() -> dict:
            return {}

        with AgentContext() as ctx:
            assert not ctx.registry.has("outside")

    def test_nested_contexts(self) -> None:
        with AgentContext() as outer:
            with AgentContext() as inner:
                assert outer.registry is not inner.registry


# ---------------------------------------------------------------------------
# Phase 4 — Workflow registry
# ---------------------------------------------------------------------------


class TestWorkflowRegistry:
    def setup_method(self) -> None:
        """Clear the workflow registry before each test."""
        from replicate_mcp.sdk import _workflow_registry  # noqa: PLC0415

        _workflow_registry.clear()

    def _make_spec(self, name: str = "test-wf") -> WorkflowSpec:
        return WorkflowBuilder(name).then("agent-a").build()

    def test_register_returns_spec(self) -> None:
        from replicate_mcp.sdk import register_workflow  # noqa: PLC0415

        spec = self._make_spec()
        returned = register_workflow(spec)
        assert returned is spec

    def test_get_workflow_registered(self) -> None:
        from replicate_mcp.sdk import get_workflow, register_workflow  # noqa: PLC0415

        spec = self._make_spec("my-wf")
        register_workflow(spec)
        assert get_workflow("my-wf") is spec

    def test_get_workflow_not_registered_returns_none(self) -> None:
        from replicate_mcp.sdk import get_workflow  # noqa: PLC0415

        assert get_workflow("nonexistent") is None

    def test_list_workflows_empty(self) -> None:
        from replicate_mcp.sdk import list_workflows  # noqa: PLC0415

        assert list_workflows() == {}

    def test_list_workflows_returns_snapshot(self) -> None:
        from replicate_mcp.sdk import list_workflows, register_workflow  # noqa: PLC0415

        spec_a = self._make_spec("wf-a")
        spec_b = self._make_spec("wf-b")
        register_workflow(spec_a)
        register_workflow(spec_b)
        snap = list_workflows()
        assert "wf-a" in snap
        assert "wf-b" in snap

    def test_list_workflows_is_copy(self) -> None:
        """Mutations to the returned dict must not affect the registry."""
        from replicate_mcp.sdk import list_workflows  # noqa: PLC0415

        snap = list_workflows()
        snap["injected"] = self._make_spec("injected")
        assert list_workflows().get("injected") is None

    def test_register_overwrites_existing(self) -> None:
        from replicate_mcp.sdk import get_workflow, register_workflow  # noqa: PLC0415

        spec1 = self._make_spec("dupe")
        spec2 = WorkflowBuilder("dupe").then("other-agent").build()
        register_workflow(spec1)
        register_workflow(spec2)
        assert get_workflow("dupe") is spec2
