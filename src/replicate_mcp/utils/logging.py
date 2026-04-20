"""Structured logging configuration.

Configures :mod:`structlog` for JSON output in production and
coloured, human-readable output in development.

Usage::

    from replicate_mcp.utils.logging import configure_logging, get_logger

    configure_logging(env="dev")  # or "prod"
    log = get_logger("my_module")
    log.info("hello", user="alice")
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

# structlog is an optional dependency — gracefully degrade to stdlib
try:
    import structlog
    from structlog.types import Processor

    HAS_STRUCTLOG = True
except ImportError:  # pragma: no cover
    HAS_STRUCTLOG = False

__all__ = ["configure_logging", "get_logger", "HAS_STRUCTLOG"]


def configure_logging(
    *,
    env: str | None = None,
    level: str = "INFO",
) -> None:
    """Set up logging for the whole process.

    Args:
        env: ``"prod"`` for JSON lines, ``"dev"`` for coloured console.
             Auto-detected from ``REPLICATE_MCP_ENV`` if not given.
        level: Root log level name (``DEBUG``, ``INFO``, ``WARNING``, etc.).
    """

    env = env or os.environ.get("REPLICATE_MCP_ENV", "dev")
    log_level = getattr(logging, level.upper(), logging.INFO)

    if HAS_STRUCTLOG:
        shared_processors: list[Processor] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
        ]

        if env == "prod":
            renderer: Processor = structlog.processors.JSONRenderer()
        else:
            renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

        structlog.configure(
            processors=[
                *shared_processors,
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

        formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )

        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)


def get_logger(name: str, **initial_context: Any) -> Any:
    """Return a bound logger.

    If structlog is installed, returns a ``structlog.BoundLogger``
    with *initial_context* bound.  Otherwise, returns a stdlib logger.
    """

    if HAS_STRUCTLOG:
        return structlog.get_logger(name, **initial_context)
    return logging.getLogger(name)