"""MCP protocol data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPTool:
    """Simplified MCP tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResource:
    """Simplified MCP resource definition."""

    name: str
    uri: str
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["MCPTool", "MCPResource"]
