"""Tests for Phase 6 error classification — is_retryable_error and exception hierarchy."""

from __future__ import annotations

from replicate_mcp.exceptions import (
    AuthenticationError,
    ClientError,
    ExecutionError,
    ModelNotFoundError,
    NonRetryableError,
    RateLimitError,
    ReplicateMCPError,
    RetryableError,
    ServerError,
)
from replicate_mcp.resilience import RetryConfig, is_retryable_error

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_retryable_error_is_replicate_mcp_error(self) -> None:
        assert issubclass(RetryableError, ReplicateMCPError)

    def test_non_retryable_error_is_replicate_mcp_error(self) -> None:
        assert issubclass(NonRetryableError, ReplicateMCPError)

    def test_rate_limit_error_is_retryable(self) -> None:
        assert issubclass(RateLimitError, RetryableError)

    def test_server_error_is_retryable(self) -> None:
        assert issubclass(ServerError, RetryableError)

    def test_authentication_error_is_non_retryable(self) -> None:
        assert issubclass(AuthenticationError, NonRetryableError)

    def test_client_error_is_non_retryable(self) -> None:
        assert issubclass(ClientError, NonRetryableError)

    def test_execution_error_is_neither(self) -> None:
        """ExecutionError is not RetryableError or NonRetryableError — falls through."""
        assert not issubclass(ExecutionError, RetryableError)
        assert not issubclass(ExecutionError, NonRetryableError)

    def test_model_not_found_error_is_neither(self) -> None:
        assert not issubclass(ModelNotFoundError, RetryableError)
        assert not issubclass(ModelNotFoundError, NonRetryableError)


# ---------------------------------------------------------------------------
# Exception attributes
# ---------------------------------------------------------------------------


class TestExceptionAttributes:
    def test_rate_limit_error_retry_after(self) -> None:
        exc = RateLimitError(retry_after=30.0)
        assert exc.retry_after == 30.0
        assert "retry after 30s" in str(exc)

    def test_rate_limit_error_no_retry_after(self) -> None:
        exc = RateLimitError()
        assert exc.retry_after is None
        assert "Rate limited" in str(exc)

    def test_server_error_status_code(self) -> None:
        exc = ServerError(status_code=503, message="Service Unavailable")
        assert exc.status_code == 503
        assert "503" in str(exc)
        assert "Service Unavailable" in str(exc)

    def test_client_error_status_code(self) -> None:
        exc = ClientError(status_code=400, message="Bad Request")
        assert exc.status_code == 400
        assert "400" in str(exc)

    def test_authentication_error_token_hint(self) -> None:
        exc = AuthenticationError(token_hint="r_abcd...wxyz")  # noqa: S106
        assert exc.token_hint == "r_abcd...wxyz"  # noqa: S105
        assert "r_abcd" in str(exc)

    def test_authentication_error_no_hint(self) -> None:
        exc = AuthenticationError()
        assert exc.token_hint == ""
        assert "Authentication failed" in str(exc)


# ---------------------------------------------------------------------------
# is_retryable_error
# ---------------------------------------------------------------------------


class TestIsRetryableError:
    def test_non_retryable_error_is_not_retryable(self) -> None:
        exc = NonRetryableError("permanent failure")
        assert is_retryable_error(exc) is False

    def test_authentication_error_is_not_retryable(self) -> None:
        exc = AuthenticationError()
        assert is_retryable_error(exc) is False

    def test_client_error_is_not_retryable(self) -> None:
        exc = ClientError(status_code=400)
        assert is_retryable_error(exc) is False

    def test_retryable_error_is_retryable(self) -> None:
        exc = RetryableError("transient")
        assert is_retryable_error(exc) is True

    def test_rate_limit_error_is_retryable(self) -> None:
        exc = RateLimitError(retry_after=10.0)
        assert is_retryable_error(exc) is True

    def test_server_error_is_retryable(self) -> None:
        exc = ServerError(status_code=500)
        assert is_retryable_error(exc) is True

    def test_unknown_exception_is_retryable_with_default_config(self) -> None:
        """Unknown exceptions fall through to the configured retryable_exceptions tuple."""
        exc = ValueError("some transient issue")
        assert is_retryable_error(exc) is True  # Exception is in default tuple

    def test_unknown_exception_not_retryable_with_strict_config(self) -> None:
        """Strict config only retries RetryableError subclasses."""
        cfg = RetryConfig(retryable_exceptions=(RetryableError,))
        exc = ValueError("some transient issue")
        assert is_retryable_error(exc, cfg) is False

    def test_model_not_found_not_retryable_with_strict_config(self) -> None:
        """ModelNotFoundError is not RetryableError, so it's not retried with strict config."""
        cfg = RetryConfig(retryable_exceptions=(RetryableError,))
        exc = ModelNotFoundError("bad_model")
        assert is_retryable_error(exc, cfg) is False

    def test_non_retryable_always_not_retryable_regardless_of_config(self) -> None:
        """Even if someone adds NonRetryableError to the tuple, it's never retried."""
        cfg = RetryConfig(retryable_exceptions=(NonRetryableError, Exception))
        exc = ClientError(status_code=403)
        assert is_retryable_error(exc, cfg) is False

    def test_retryable_always_retryable_regardless_of_config(self) -> None:
        """RetryableError is always retried even if not in the tuple."""
        cfg = RetryConfig(retryable_exceptions=(ValueError,))
        exc = RateLimitError()
        assert is_retryable_error(exc, cfg) is True

    def test_runtime_error_is_retryable_with_default_config(self) -> None:
        exc = RuntimeError("transient")
        assert is_retryable_error(exc) is True  # RuntimeError is Exception subclass

    def test_keyboard_interrupt_not_retryable_with_strict_config(self) -> None:
        """KeyboardInterrupt is not in the strict retryable_exceptions tuple."""
        cfg = RetryConfig(retryable_exceptions=(RetryableError,))
        exc = KeyboardInterrupt()
        assert is_retryable_error(exc, cfg) is False
