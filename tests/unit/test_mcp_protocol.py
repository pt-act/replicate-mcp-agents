"""Unit tests for the MCP protocol data structures."""

from __future__ import annotations

from replicate_mcp.mcp.protocol import MCPResource, MCPTool
from replicate_mcp.mcp.transport import TransportConfig


class TestMCPTool:
    """Tests for MCPTool dataclass."""

    def test_minimal_construction(self) -> None:
        tool = MCPTool(name="echo", description="Echo input")
        assert tool.name == "echo"
        assert tool.description == "Echo input"
        assert tool.input_schema == {}
        assert tool.annotations == {}

    def test_with_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        }
        tool = MCPTool(name="chat", description="Chat", input_schema=schema)
        assert tool.input_schema["type"] == "object"
        assert "prompt" in tool.input_schema["properties"]

    def test_with_annotations(self) -> None:
        tool = MCPTool(
            name="t",
            description="d",
            annotations={"readOnlyHint": True, "destructiveHint": False},
        )
        assert tool.annotations["readOnlyHint"] is True

    def test_defaults_are_independent(self) -> None:
        """Ensure default_factory gives each instance its own dict."""
        t1 = MCPTool(name="a", description="a")
        t2 = MCPTool(name="b", description="b")
        t1.input_schema["added"] = True
        assert "added" not in t2.input_schema


class TestMCPResource:
    """Tests for MCPResource dataclass."""

    def test_minimal_construction(self) -> None:
        r = MCPResource(name="greeting", uri="greeting://world")
        assert r.name == "greeting"
        assert r.uri == "greeting://world"
        assert r.metadata == {}

    def test_with_metadata(self) -> None:
        r = MCPResource(name="r", uri="r://x", metadata={"version": 1})
        assert r.metadata["version"] == 1

    def test_defaults_are_independent(self) -> None:
        r1 = MCPResource(name="a", uri="a://")
        r2 = MCPResource(name="b", uri="b://")
        r1.metadata["added"] = True
        assert "added" not in r2.metadata


class TestTransportConfig:
    """Tests for TransportConfig dataclass."""

    def test_defaults(self) -> None:
        cfg = TransportConfig()
        assert cfg.transport == "stdio"
        assert cfg.log_level == "info"

    def test_custom_values(self) -> None:
        cfg = TransportConfig(transport="sse", log_level="debug")
        assert cfg.transport == "sse"
        assert cfg.log_level == "debug"
