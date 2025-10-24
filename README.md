# Replicate MCP Agents

Replicate MCP Agents transforms Replicate models into composable, stateful agents that can be orchestrated through the [Model Context Protocol](https://modelcontextprotocol.io/). The project ships two primary interfaces:

- **`replicate-agent` CLI** – a command-line entrypoint for discovering, executing, and monitoring multi-agent workflows built on Replicate models.
- **`replicate-mcp-server`** – an MCP server that exposes Replicate models and declarative workflows as MCP-compliant tools, ready for clients such as Claude Desktop, Cursor, or custom IDE integrations.

## Key Capabilities

- **Agent Registry**: Dynamically discover Replicate models and expose them as MCP tools with rich metadata (cost, latency, streaming support).
- **Workflow Engine**: Author declarative multi-agent graphs with checkpoint-based resumability, parallel dispatch, and fault-tolerant execution.
- **CLI Orchestration**: Run workflows from the terminal with live streaming output, progress indicators, checkpoint resumes, and artifact capture.
- **Performance Telemetry**: Track cost, latency, and token utilisation for every agent invocation to inform routing decisions.
- **MCP Native**: Seamlessly register with MCP-compatible clients so that tools appear automatically without manual configuration.

## Project Structure

```
replicate-mcp-agents/
├── src/
│   └── replicate_mcp/
│       ├── cli/                # CLI entrypoints and commands
│       ├── agents/             # Model registry, execution primitives
│       ├── mcp/                # Protocol helpers and transport adapters
│       └── utils/              # Checkpointing, telemetry, shared helpers
├── tests/                      # Unit and integration tests
├── docs/                       # MkDocs documentation site
└── examples/                   # Sample workflows and notebooks
```

## Getting Started

```bash
# Clone and install dependencies
git clone https://github.com/your-org/replicate-mcp-agents.git
cd replicate-mcp-agents
poetry install --with dev,mcp,docs

# Run the test suite
poetry run pytest

# Start the CLI
poetry run replicate-agent --help

# Launch the MCP server over stdio
poetry run replicate-mcp-server
```

### Prerequisites

- Python 3.11+
- A Replicate API token exported as `REPLICATE_API_TOKEN`

## Documentation

Project documentation is published via MkDocs. To launch the docs locally:

```bash
poetry run mkdocs serve
```

## License

Licensed under the [Apache License, Version 2.0](LICENSE).