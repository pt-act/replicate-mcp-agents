"""Tests for replicate_mcp.validation — Pydantic v2 input models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from replicate_mcp.validation import (
    AgentInputModel,
    AgentMetadataModel,
    DSLExpressionModel,
    ServerConfigModel,
    WorkflowInputModel,
)

# ---------------------------------------------------------------------------
# AgentInputModel
# ---------------------------------------------------------------------------


class TestAgentInputModel:
    def test_valid_minimal(self) -> None:
        m = AgentInputModel(agent_id="llama3", payload={})
        assert m.agent_id == "llama3"
        assert m.payload == {}
        assert m.stream is False
        assert m.timeout_s == 120.0

    def test_valid_with_payload(self) -> None:
        m = AgentInputModel(agent_id="flux_pro", payload={"prompt": "hello"})
        assert m.payload == {"prompt": "hello"}

    def test_valid_with_stream(self) -> None:
        m = AgentInputModel(agent_id="llama3", stream=True)
        assert m.stream is True

    def test_invalid_agent_id_empty(self) -> None:
        with pytest.raises(ValidationError):
            AgentInputModel(agent_id="")

    def test_invalid_agent_id_starts_with_number(self) -> None:
        with pytest.raises(ValidationError, match="agent_id"):
            AgentInputModel(agent_id="9invalid")

    def test_invalid_agent_id_too_long(self) -> None:
        with pytest.raises(ValidationError):
            AgentInputModel(agent_id="a" * 65)

    def test_invalid_timeout_zero(self) -> None:
        with pytest.raises(ValidationError):
            AgentInputModel(agent_id="ok", timeout_s=0)

    def test_invalid_timeout_too_large(self) -> None:
        with pytest.raises(ValidationError):
            AgentInputModel(agent_id="ok", timeout_s=9999)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AgentInputModel(agent_id="ok", unknown_field="x")

    def test_payload_too_large(self) -> None:
        big = {"data": "x" * 2_000_000}
        with pytest.raises(ValidationError, match="1 MiB"):
            AgentInputModel(agent_id="ok", payload=big)

    def test_agent_id_with_hyphen(self) -> None:
        m = AgentInputModel(agent_id="my-agent")
        assert m.agent_id == "my-agent"

    def test_agent_id_with_underscore(self) -> None:
        m = AgentInputModel(agent_id="my_agent")
        assert m.agent_id == "my_agent"


# ---------------------------------------------------------------------------
# WorkflowInputModel
# ---------------------------------------------------------------------------


class TestWorkflowInputModel:
    def test_valid_minimal(self) -> None:
        m = WorkflowInputModel(workflow_name="my-workflow")
        assert m.workflow_name == "my-workflow"
        assert m.initial_input == {}
        assert m.max_concurrency == 4

    def test_valid_with_checkpoint(self, tmp_path: Path) -> None:
        m = WorkflowInputModel(
            workflow_name="wf",
            checkpoint_dir=tmp_path,
            resume_from="node_a",
        )
        assert m.resume_from == "node_a"

    def test_resume_without_checkpoint_fails(self) -> None:
        with pytest.raises(ValidationError, match="checkpoint_dir"):
            WorkflowInputModel(workflow_name="wf", resume_from="node_a")

    def test_max_concurrency_bounds(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowInputModel(workflow_name="wf", max_concurrency=0)
        with pytest.raises(ValidationError):
            WorkflowInputModel(workflow_name="wf", max_concurrency=65)

    def test_max_concurrency_valid(self) -> None:
        m = WorkflowInputModel(workflow_name="wf", max_concurrency=8)
        assert m.max_concurrency == 8

    def test_empty_workflow_name_fails(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowInputModel(workflow_name="")


# ---------------------------------------------------------------------------
# AgentMetadataModel
# ---------------------------------------------------------------------------


class TestAgentMetadataModel:
    def test_valid_minimal(self) -> None:
        m = AgentMetadataModel(safe_name="my_agent", description="A test agent.")
        assert m.safe_name == "my_agent"
        assert m.model is None

    def test_valid_with_model(self) -> None:
        m = AgentMetadataModel(
            safe_name="llama3",
            description="LLM",
            model="meta/meta-llama-3-70b-instruct",
        )
        assert m.model == "meta/meta-llama-3-70b-instruct"

    def test_invalid_safe_name(self) -> None:
        with pytest.raises(ValidationError, match="safe_name"):
            AgentMetadataModel(safe_name="bad name!", description="x")

    def test_invalid_model_path(self) -> None:
        with pytest.raises(ValidationError, match="model"):
            AgentMetadataModel(
                safe_name="ok", description="ok", model="not-a-valid-path"
            )

    def test_invalid_estimated_cost_negative(self) -> None:
        with pytest.raises(ValidationError):
            AgentMetadataModel(safe_name="ok", description="ok", estimated_cost=-1.0)

    def test_invalid_tag_empty(self) -> None:
        with pytest.raises(ValidationError):
            AgentMetadataModel(safe_name="ok", description="ok", tags=[""])

    def test_valid_tags(self) -> None:
        m = AgentMetadataModel(
            safe_name="ok", description="ok", tags=["text", "chat"]
        )
        assert m.tags == ["text", "chat"]

    def test_model_with_version(self) -> None:
        m = AgentMetadataModel(
            safe_name="ok",
            description="ok",
            model="owner/model:abc123",
        )
        assert m.model == "owner/model:abc123"

    def test_extra_fields_ignored(self) -> None:
        m = AgentMetadataModel(
            safe_name="ok", description="ok", unknown_field="ignored"
        )
        assert not hasattr(m, "unknown_field")


# ---------------------------------------------------------------------------
# ServerConfigModel
# ---------------------------------------------------------------------------


class TestServerConfigModel:
    def test_valid_defaults(self) -> None:
        m = ServerConfigModel()
        assert m.transport == "stdio"
        assert m.log_level == "INFO"
        assert m.max_concurrency == 8

    def test_invalid_transport(self) -> None:
        with pytest.raises(ValidationError, match="transport"):
            ServerConfigModel(transport="grpc")

    def test_valid_sse_transport(self) -> None:
        m = ServerConfigModel(transport="sse")
        assert m.transport == "sse"

    def test_log_level_normalised(self) -> None:
        m = ServerConfigModel(log_level="debug")
        assert m.log_level == "DEBUG"

    def test_invalid_log_level(self) -> None:
        with pytest.raises(ValidationError):
            ServerConfigModel(log_level="VERBOSE")

    def test_max_concurrency_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ServerConfigModel(max_concurrency=0)
        with pytest.raises(ValidationError):
            ServerConfigModel(max_concurrency=257)


# ---------------------------------------------------------------------------
# DSLExpressionModel
# ---------------------------------------------------------------------------


class TestDSLExpressionModel:
    def test_valid(self) -> None:
        m = DSLExpressionModel(expression="x + 1", context={"x": 5})
        assert m.expression == "x + 1"

    def test_empty_expression_fails(self) -> None:
        with pytest.raises(ValidationError):
            DSLExpressionModel(expression="")

    def test_too_long_expression_fails(self) -> None:
        with pytest.raises(ValidationError):
            DSLExpressionModel(expression="x" * 513)
