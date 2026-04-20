"""Unit tests for the structured logging configuration."""

from __future__ import annotations

import logging

from replicate_mcp.utils.logging import configure_logging, get_logger, HAS_STRUCTLOG


class TestConfigureLogging:
    """Tests for configure_logging()."""

    def test_dev_mode(self) -> None:
        configure_logging(env="dev", level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1

    def test_prod_mode(self) -> None:
        configure_logging(env="prod", level="WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_default_env(self) -> None:
        """Without explicit env, should default to 'dev'."""
        configure_logging(level="INFO")
        root = logging.getLogger()
        assert root.level == logging.INFO


class TestGetLogger:
    """Tests for get_logger()."""

    def test_returns_logger(self) -> None:
        log = get_logger("test_module")
        assert log is not None

    def test_stdlib_fallback(self) -> None:
        """If structlog is not available, get_logger returns stdlib."""
        if not HAS_STRUCTLOG:
            log = get_logger("fallback")
            assert isinstance(log, logging.Logger)