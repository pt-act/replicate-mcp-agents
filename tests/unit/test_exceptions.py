"""Unit tests for the domain exception hierarchy."""

from __future__ import annotations

import pytest

from replicate_mcp.exceptions import (
    AgentNotFoundError,
    AuthenticationError,
    CheckpointCorruptedError,
    ClientError,
    CycleDetectedError,
    DuplicateAgentError,
    ExecutionError,
    ExecutionTimeoutError,
    ModelNotFoundError,
    NodeNotFoundError,
    NonRetryableError,
    RateLimitError,
    ReplicateMCPError,
    RetryableError,
    ServerError,
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
            # Phase 6 — error classification
            RetryableError,
            NonRetryableError,
            RateLimitError,
            ServerError,
            AuthenticationError,
            ClientError,
        ],
    )
    def test_subclass(self, exc_class: type[ReplicateMCPError]) -> None:
        assert issubclass(exc_class, ReplicateMCPError)

    def test_execution_timeout_is_execution_error(self) -> None:
        assert issubclass(ExecutionTimeoutError, ExecutionError)

    def test_rate_limit_is_retryable(self) -> None:
        assert issubclass(RateLimitError, RetryableError)

    def test_server_error_is_retryable(self) -> None:
        assert issubclass(ServerError, RetryableError)

    def test_authentication_is_non_retryable(self) -> None:
        assert issubclass(AuthenticationError, NonRetryableError)

    def test_client_error_is_non_retryable(self) -> None:
        assert issubclass(ClientError, NonRetryableError)


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


# ---------------------------------------------------------------------------
# Phase 6 — Error classification attributes
# ---------------------------------------------------------------------------


class TestRetryableError:
    def test_message(self) -> None:
        err = RetryableError("transient failure")
        assert "transient failure" in str(err)

    def test_is_replicate_mcp_error(self) -> None:
        assert isinstance(RetryableError("x"), ReplicateMCPError)


class TestNonRetryableError:
    def test_message(self) -> None:
        err = NonRetryableError("permanent failure")
        assert "permanent failure" in str(err)

    def test_is_replicate_mcp_error(self) -> None:
        assert isinstance(NonRetryableError("x"), ReplicateMCPError)


class TestRateLimitError:
    def test_default_retry_after_is_none(self) -> None:
        err = RateLimitError()
        assert err.retry_after is None

    def test_message_without_retry_after(self) -> None:
        err = RateLimitError()
        assert str(err) == "Rate limited"

    def test_message_with_retry_after(self) -> None:
        err = RateLimitError(retry_after=60.0)
        assert "retry after 60s" in str(err)
        assert err.retry_after == 60.0

    def test_zero_retry_after(self) -> None:
        err = RateLimitError(retry_after=0.0)
        assert err.retry_after == 0.0
        assert "retry after 0s" in str(err)


class TestServerError:
    def test_default_status_code(self) -> None:
        err = ServerError()
        assert err.status_code == 500
        assert err.message == ""

    def test_message_with_defaults(self) -> None:
        err = ServerError()
        assert "Server error 500" in str(err)

    def test_custom_status_code(self) -> None:
        err = ServerError(status_code=503)
        assert err.status_code == 503
        assert "503" in str(err)

    def test_message_with_detail(self) -> None:
        err = ServerError(status_code=502, message="Bad Gateway")
        assert err.message == "Bad Gateway"
        assert "Bad Gateway" in str(err)

    def test_message_without_detail(self) -> None:
        err = ServerError(status_code=500, message="")
        assert str(err) == "Server error 500"


class TestAuthenticationError:
    def test_default_token_hint(self) -> None:
        err = AuthenticationError()
        assert err.token_hint == ""

    def test_message_without_hint(self) -> None:
        err = AuthenticationError()
        assert str(err) == "Authentication failed"

    def test_message_with_hint(self) -> None:
        err = AuthenticationError(token_hint="r_abcd")  # noqa: S106
        assert "r_abcd" in str(err)
        assert err.token_hint == "r_abcd"  # noqa: S105


class TestClientError:
    def test_default_status_code(self) -> None:
        err = ClientError()
        assert err.status_code == 400
        assert err.message == ""

    def test_message_with_defaults(self) -> None:
        err = ClientError()
        assert "Client error 400" in str(err)

    def test_custom_status_code(self) -> None:
        err = ClientError(status_code=403)
        assert err.status_code == 403
        assert "403" in str(err)

    def test_message_with_detail(self) -> None:
        err = ClientError(status_code=422, message="Unprocessable Entity")
        assert err.message == "Unprocessable Entity"
        assert "Unprocessable Entity" in str(err)

    def test_message_without_detail(self) -> None:
        err = ClientError(status_code=400, message="")
        assert str(err) == "Client error 400"
