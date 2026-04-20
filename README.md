# Replicate MCP Agents

Bridge between [Replicate](https://replicate.com) models and the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), enabling AI models hosted on Replicate to appear as MCP-compliant tools in clients such as Claude Desktop and Cursor.

## Current Status — v0.1.0 (pre-alpha)

| Component | Status | Notes |
|-----------|--------|-------|
| MCP Server (stdio) | ✅ Functional | Registers Replicate models as MCP tools via the official `mcp` SDK |
| Replicate Integration | ✅ Functional | `AgentExecutor` calls the Replicate API with streaming support |
| Agent Registry | ✅ Functional | In-memory registry with metadata (cost, latency, schema) |
| CLI (`replicate-agent`) | ⚠️ Scaffold | `init`, `run`, `workflows list` commands — placeholder execution |
| Workflow Engine (DAG) | ⚠️ Data model only | `AgentNode`, `WorkflowEdge`, `AgentWorkflow` dataclasses defined |
| Checkpoint Persistence | ✅ Functional | Filesystem-based JSON checkpoints via `CheckpointManager` |
| Telemetry Tracking | ✅ Functional | In-memory cost/latency accumulator |
| Safe Transform Registry | ✅ Functional | Named transforms replace string lambdas — no `eval()` |
| Parallel Execution | 🔲 Planned | Topological sort and fan-out not yet implemented |
| Cost-Aware Routing | 🔲 Planned | Multi-objective routing algorithm not yet implemented |
| Plugin System | 🔲 Planned | Entry-point based architecture not yet implemented |

## Project Structure

```
replicate-mcp-agents/
├── src/replicate_mcp/
│   ├── server.py           # MCP server entrypoint (FastMCP + Replicate)
│   ├── cli/                # Click-based CLI
│   ├── agents/
│   │   ├── registry.py     # Agent metadata & discovery
│   │   ├── execution.py    # Replicate API executor with streaming
│   │   ├── composition.py  # Workflow graph data model
│   │   └── transforms.py   # Safe callable registry (no eval!)
│   ├── mcp/                # MCP protocol data structures
│   └── utils/              # Checkpointing, telemetry
├── tests/unit/             # 72 unit tests
├── docs/                   # MkDocs site + ADRs
└── examples/workflows/     # YAML workflow definitions
```

## Getting Started

### Prerequisites

- Python 3.10+
- A Replicate API token (`REPLICATE_API_TOKEN`)

### Installation

```bash
git clone https://github.com/pt-act/replicate-mcp-agents.git
cd replicate-mcp-agents
poetry install --with dev,docs

# Run the test suite (72 tests)
poetry run pytest

# Start the CLI
poetry run replicate-agent --help

# Launch the MCP server over stdio
export REPLICATE_API_TOKEN=r8_your_token_here
poetry run replicate-mcp-server
```

### Claude Desktop Integration

Add to `~/.config/claude/mcp_config.json`:

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

## Roadmap

See the [Implementation Plan](docs/adr/001-dependency-cleanup.md) and project board for detailed sprint-by-sprint progress.

**Phase 1 (Weeks 1–8):** Foundation — MCP server, Replicate integration, DAG engine, test infrastructure  
**Phase 2 (Weeks 9–16):** Hardening — abstractions, routing, resilience, observability, security  
**Phase 3 (Weeks 17–24):** Differentiation — discovery, SDK, plugins, distributed execution  

## License

Licensed under the [Apache License, Version 2.0](LICENSE).