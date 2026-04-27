# Getting Started

This guide walks you through installing Replicate MCP Agents, configuring your environment, and running your first workflow.

## Prerequisites

- Python 3.11 or higher
- A Replicate API token exported as `REPLICATE_API_TOKEN`
- [Poetry](https://python-poetry.org/) 2.x or newer

## Installation

```bash
git clone https://github.com/your-org/replicate-mcp-agents.git
cd replicate-mcp-agents
poetry install --with dev,mcp,docs
```

## Validate the Installation

```bash
poetry run pytest
poetry run replicate-agent --help
```

## Next Steps

- Review the [Workflow Guide](workflow-guide.md) to author your first pipeline.
- Register the MCP server following [MCP Integration](mcp-integration.md).