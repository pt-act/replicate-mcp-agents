"""Unit tests for the domain exception hierarchy."""

from __future__ import annotations

import pytest

from replicate_mcp.exceptions import (
    AgentNotFoundError,
    CheckpointCorruptedError,
    CycleDetectedError,
    DuplicateAgentError,
    ExecutionError,
    ExecutionTimeoutError,
    ModelNotFoundError,
    NodeNotFoundError,
    ReplicateMCPError,
    TokenNotSetError,
    WorkflowValidationError,
)


class TestExceptionHierarchy:
    """Verify all exceptions inherit from ReplicateMCPError."""

    @pytest.mark.parametrize(
        "exc_class",
        [
            CycleDetectedError,
            NodeNotFoundError,
            WorkflowValidationError,
            ModelNotFoundError,
            ExecutionError,
            ExecutionTimeoutError,
            TokenNotSetError,
            DuplicateAgentError,
            AgentNotFoundError,
            CheckpointCorruptedError,
        ],
    )
    def test_subclass(self, exc_class) -> None:
        assert issubclass(exc_class, ReplicateMCPError)

    def test_execution_timeout_is_execution_error(self) -> None:
        assert issubclass(ExecutionTimeoutError, ExecutionError)


class TestCycleDetectedError:
    def test_message_contains_path(self) -> None:
        err = CycleDetectedError(["a", "b", "c", "a"])
        assert "a → b → c → a" in str(err)
        assert err.cycle == ["a", "b", "c", "a"]


class TestNodeNotFoundError:
    def test_message(self) -> None:
        err = NodeNotFoundError("xyz")
        assert "xyz" in str(err)
        assert err.node_id == "xyz"


class TestModelNotFoundError:
    def test_without_available(self) -> None:
        err = ModelNotFoundError("foo")
        assert "foo" in str(err)
        assert err.available == []

    def test_with_available(self) -> None:
        err = ModelNotFoundError("foo", ["bar", "baz"])
        assert "bar" in str(err)
        assert err.model_id == "foo"


class TestExecutionError:
    def test_without_cause(self) -> None:
        err = ExecutionError("m/x")
        assert "m/x" in str(err)
        assert err.cause is None

    def test_with_cause(self) -> None:
        cause = RuntimeError("boom")
        err = ExecutionError("m/x", cause)
        assert "RuntimeError" in str(err)
        assert err.cause is cause


class TestTokenNotSetError:
    def test_message(self) -> None:
        err = TokenNotSetError()
        assert "REPLICATE_API_TOKEN" in str(err)


class TestDuplicateAgentError:
    def test_message(self) -> None:
        err = DuplicateAgentError("my_agent")
        assert "my_agent" in str(err)


class TestCheckpointCorruptedError:
    def test_without_cause(self) -> None:
        err = CheckpointCorruptedError("sess-1")
        assert "sess-1" in str(err)

    def test_with_cause(self) -> None:
        err = CheckpointCorruptedError("sess-1", ValueError("bad json"))
        assert "bad json" in str(err)
