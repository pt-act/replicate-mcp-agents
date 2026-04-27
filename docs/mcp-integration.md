# MCP Integration

This document explains how to register the Replicate MCP server with MCP-compatible clients such as Claude Desktop or Cursor.

## Claude Desktop

Update `~/.config/claude/mcp_config.json` with the following entry:

```json
{
  "mcpServers": {
    "replicate-agent": {
      "command": "poetry",
      "args": ["run", "replicate-mcp-server"],
      "env": {
        "REPLICATE_API_TOKEN": "${REPLICATE_API_TOKEN}"
      }
    }
  }
}
```

Restart Claude Desktop and open the tool palette to confirm that the Replicate workflows are available.

## CLI Transport

By default the server uses stdio transport. If you need HTTP or WebSocket transports, configure them in `~/.replicate/mcp.yaml` (created via `replicate-agent init`).