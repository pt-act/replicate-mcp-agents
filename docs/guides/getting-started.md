# Getting Started

**Time to first result: ≤ 30 minutes.**

This guide walks you from a fresh Python environment to running your first
multi-model workflow via the Replicate MCP Agents platform.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.10 |
| Replicate API token | [replicate.com/account](https://replicate.com/account) |

---

## Step 1 — Install

```bash
pip install replicate-mcp-agents
```

For OpenTelemetry observability support:

```bash
pip install "replicate-mcp-agents[otel]"
```

---

## Step 2 — Set your API token

```bash
export REPLICATE_API_TOKEN=r8_...
```

Or place it in a `.env` file (the SDK picks it up automatically via
`SecretManager`).

---

## Step 3 — Define your first agent (2 minutes)

```python
# my_agents.py
from replicate_mcp.sdk import agent, AgentBuilder

# Option A — decorator (simplest)
@agent(model="meta/llama-3-8b-instruct", description="Fast chat model")
def llama_chat(prompt: str) -> dict:
    """Invoke LLaMA 3 8B for fast chat completions."""
    return {"prompt": prompt}


# Option B — fluent builder
summariser = (
    AgentBuilder("summariser")
    .model("mistral/mixtral-8x7b-instruct")
    .description("High-quality long-document summariser")
    .tag("nlp", "summarise")
    .streaming(True)
    .estimated_cost(0.003)
    .build()
)
```

---

## Step 4 — Run the agent via the CLI (3 minutes)

```bash
# List all registered agents
replicate-agent agents list

# Invoke an agent
replicate-agent agents run llama_chat --payload '{"prompt": "Explain async/await in Python"}'
```

Expected output:

```
✓ Agent llama_chat completed in 3.2s
{"done": true, "output": "Async/await is a syntax for writing..."}
```

---

## Step 5 — Auto-discover models from Replicate (5 minutes)

Instead of hand-registering every model, let `ModelDiscovery` fill your
registry from the live Replicate catalog:

```python
import asyncio
from replicate_mcp.discovery import ModelDiscovery, DiscoveryConfig
from replicate_mcp.agents.registry import AgentRegistry

async def main() -> None:
    config = DiscoveryConfig(
        owner="meta",          # Only Meta's models
        max_models=10,
        ttl_seconds=300,       # Cache for 5 minutes
    )
    registry = AgentRegistry()
    disc = ModelDiscovery(registry=registry, config=config)

    result = await disc.refresh()
    print(f"Discovered {result.discovered} models in {result.elapsed_ms:.0f} ms")

    for name, agent in registry.list_agents().items():
        print(f"  {name}: {agent.description[:60]}")

asyncio.run(main())
```

---

## Step 6 — Quality-aware routing (5 minutes)

`UCB1Router` automatically balances exploration and exploitation across
your model pool:

```python
from replicate_mcp.qos import UCB1Router, QoSPolicy, QoSLevel

router = UCB1Router()
router.register_model("meta/llama-3-8b-instruct", initial_cost=0.001)
router.register_model("mistral/mixtral-8x7b-instruct", initial_cost=0.003)

# Apply a QoS tier — only consider models with latency < 2 s
policy = QoSPolicy.for_level(QoSLevel.FAST)
candidates = ["meta/llama-3-8b-instruct", "mistral/mixtral-8x7b-instruct"]
chosen = router.select_model_with_policy(candidates, policy=policy)
print(f"Router selected: {chosen}")

# After execution, feed back the result so the router learns:
router.record_outcome(chosen, latency_ms=1800, cost_usd=0.0012, success=True)
```

---

## Step 7 — Scale with 2 nodes (5 minutes)

```python
import asyncio
from replicate_mcp.distributed import DistributedExecutor, WorkerNode

async def main() -> None:
    async with DistributedExecutor() as executor:
        executor.add_node(WorkerNode("worker-1"))
        executor.add_node(WorkerNode("worker-2"))

        # Submit 10 tasks — automatically distributed across both nodes
        tasks = [
            ("llama_chat", {"prompt": f"Question {i}"})
            for i in range(10)
        ]
        results = await executor.run_many(tasks)

        for r in results:
            print(f"[{r.node_id}] {r.agent_name}: {r.status.value}")

asyncio.run(main())
```

---

## Step 8 — Add a plugin (5 minutes)

Plugins extend the platform without modifying core code.  Here is a
minimal cost-tracking plugin:

```python
# my_plugin.py
from replicate_mcp.plugins import BasePlugin, PluginMetadata, PluginRegistry
from typing import Any

class CostTrackerPlugin(BasePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="cost_tracker", version="1.0.0")

    def setup(self) -> None:
        self.total_cost = 0.0

    def teardown(self) -> None:
        print(f"Total estimated cost: ${self.total_cost:.4f}")

    def on_agent_result(self, agent_name: str, chunks: list[Any], latency_ms: float) -> None:
        # Accumulate cost from result metadata
        for chunk in chunks:
            self.total_cost += chunk.get("cost_usd", 0.0)


# Usage:
plugin = CostTrackerPlugin()
registry = PluginRegistry()
registry.load(plugin)
# ... run agents ...
registry.unload_all()  # prints total cost
```

To make your plugin pip-installable, add to your `pyproject.toml`:

```toml
[project.entry-points."replicate_mcp.plugins"]
cost_tracker = "my_package.my_plugin:CostTrackerPlugin"
```

---

## Step 9 — Start the MCP server (2 minutes)

Connect to Claude Desktop or Cursor by starting the MCP server:

```bash
replicate-mcp-server
```

Then add to your Claude Desktop `config.json`:

```json
{
  "mcpServers": {
    "replicate": {
      "command": "replicate-mcp-server",
      "env": {"REPLICATE_API_TOKEN": "r8_..."}
    }
  }
}
```

All registered agents automatically appear as MCP tools in Claude.

---

## What's next?

| Topic | Document |
|---|---|
| Workflow authoring (YAML + Python) | `docs/guides/workflows.md` |
| Plugin development guide | `docs/guides/plugins.md` |
| Distributed execution deep-dive | `docs/guides/distributed.md` |
| Operations runbook | `docs/runbooks/top-10-failures.md` |
| API reference | `docs/api/` |
| Architecture decisions | `docs/adr/` |