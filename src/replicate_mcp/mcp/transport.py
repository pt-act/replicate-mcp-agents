"""Transport configuration utilities for MCP server."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransportConfig:
    """Represents how the MCP server communicates with clients."""

    transport: str = "stdio"
    log_level: str = "info"


__all__ = ["TransportConfig"]
