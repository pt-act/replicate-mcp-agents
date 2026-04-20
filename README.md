# Replicate MCP Agents

Bridge between [Replicate](https://replicate.com) models and the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), enabling AI models hosted on Replicate to appear as MCP-compliant tools in clients such as Claude Desktop and Cursor.

## Current Status — v0.2.0 (alpha)

| Component | Status | Notes |
|-----------|--------|-------|
| MCP Server (stdio) | ✅ Functional | Registers agents as MCP tools + `models://list` resource |
| Replicate Integration | ✅ Functional | `AgentExecutor` with streaming, retry, concurrency limiter |
| Agent Registry | ✅ v2 | Dict-backed, O(1) lookup, dedup, get/remove/filter/clear |
| DAG Workflow Engine | ✅ Functional | Kahn's topological sort, DFS 3-colour cycle detection |
| Parallel Fan-Out | ✅ Functional | `anyio.create_task_group()` runs same-level nodes concurrently |
| Edge Transforms/Conditions | ✅ Functional | Wired to `TransformRegistry`; applied during execution |
| Checkpoint Persistence | ✅ v2 | Atomic writes (`os.replace`), versioning, list/delete |
| Telemetry Tracking | ✅ Functional | In-memory cost/latency accumulator |
| Safe Transform Registry | ✅ Functional | Named transforms replace string lambdas — no `eval()` |
| Model Catalogue | ✅ Functional | `discover()` hydration from Replicate API with TTL cache |
| Exception Hierarchy | ✅ Functional | 13 domain exceptions under `ReplicateMCPError` |
| Structured Logging | ✅ Functional | structlog (JSON prod, coloured dev) + stdlib fallback |
| CLI (`replicate-agent`) | ⚠️ Scaffold | `init`, `run`, `workflows list` — placeholder execution |
| Cost-Aware Routing | 🔲 Planned | Multi-objective routing algorithm |
| Plugin System | 🔲 Planned | Entry-point based architecture |
| Distributed Execution | 🔲 Planned | Redis Streams / NATS + K8s |

**Test suite:** 164 tests, 83% coverage, 0.7s.

## Project Structure

```
replicate-mcp-agents/
├── src/replicate_mcp/
│   ├── __init__.py
│   ├── server.py             # MCP server entrypoint (FastMCP + Replicate)
│   ├── exceptions.py         # Domain exception hierarchy
│   ├── cli/                  # Click-based CLI
│   ├── agents/
│   │   ├── registry.py       # Agent metadata & discovery (v2, dict-backed)
│   │   ├── execution.py      # Replicate API executor (retry, semaphore)
│   │   ├── composition.py    # DAG engine (topo sort, cycle detect, fan-out)
│   │   └── transforms.py     # Safe callable registry (no eval!)
│   ├── mcp/                  # MCP protocol data structures
│   └── utils/
│       ├── checkpointing.py  # Atomic checkpoint persistence
│       ├── telemetry.py      # Cost/latency tracking
│       └── logging.py        # structlog configuration
├── tests/
│   ├── unit/                 # 155 unit tests
│   └── integration/          # 9 integration tests (mocked API)
├── docs/                     # MkDocs site + ADRs
└── examples/workflows/       # YAML workflow definitions
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

# Run the test suite (164 tests)
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

### Workflow Example (Python)

```python
from replicate_mcp.agents import AgentNode, AgentWorkflow, WorkflowEdge

wf = (
    AgentWorkflow(name="research", description="Multi-model pipeline")
    .add_agent("search", AgentNode(model_id="perplexity/sonar-large-online", role="specialist"))
    .add_agent("analyst", AgentNode(model_id="openai/gpt-4.1-mini", role="analyst"))
    .add_agent("writer", AgentNode(model_id="anthropic/claude-4.5-sonnet", role="summariser"))
)
wf.add_edge(WorkflowEdge(from_agent="search", to_agent="analyst"))
wf.add_edge(WorkflowEdge(from_agent="analyst", to_agent="writer"))

# Execution is level-by-level with concurrent fan-out
async for event in wf.execute({"query": "MCP protocol"}):
    print(event["node"], event["output"])
```

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Cycle detection | DFS 3-colour on `add_edge()` | Fail-fast; no broken DAGs ever enter the system |
| Parallel execution | `anyio` task groups | Framework-agnostic; works with asyncio and trio |
| Checkpoints | `tempfile` + `os.replace()` | POSIX-atomic; no corruption on crash |
| Transforms | Named registry, no `eval()` | CWE-94 prevention; injection-proof |
| Retry | Decorrelated jitter backoff | Prevents thundering herd on Replicate API |
| Logging | structlog with stdlib fallback | JSON in prod, coloured in dev; zero-dep graceful degradation |

## Roadmap

**Phase 1 (Weeks 1–8):** Foundation — ✅ Sprints S1–S4 complete  
**Phase 2 (Weeks 9–16):** Hardening — abstractions, routing, resilience, observability, security  
**Phase 3 (Weeks 17–24):** Differentiation — discovery, SDK, plugins, distributed execution  

See [CHANGELOG.md](CHANGELOG.md) for detailed release notes.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).