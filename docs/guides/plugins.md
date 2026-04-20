# Plugin Development Guide

Plugins extend replicate-mcp-agents without modifying the core package.
They are distributed as regular Python packages and discovered automatically
via [Python entry points](https://packaging.python.org/en/latest/specifications/entry-points/).

---

## Plugin lifecycle

```
pip install my-plugin
      │
      ▼
load_plugins()          discovers entry-points → instantiates classes
      │
      ▼
PluginRegistry.load()   calls plugin.setup()
      │
      ▼
executor.run()          dispatches hooks:
                        ├── on_agent_run(agent_name, payload)
                        ├── on_agent_result(agent_name, chunks, latency_ms)
                        └── on_error(agent_name, error)
      │
      ▼
PluginRegistry.unload() calls plugin.teardown()
```

---

## Minimal plugin

```python
# src/my_plugin/plugin.py
from typing import Any
from replicate_mcp.plugins import BasePlugin, PluginMetadata


class MyPlugin(BasePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="my_plugin",
            version="1.0.0",
            description="Does something useful",
            author="Your Name <you@example.com>",
        )

    def setup(self) -> None:
        # Initialise resources (open DB connection, read config, etc.)
        pass

    def teardown(self) -> None:
        # Release resources (close DB connection, flush buffer, etc.)
        pass

    # Optional hooks — override only what you need:

    def on_agent_run(self, agent_name: str, payload: dict[str, Any]) -> None:
        print(f"▶ {agent_name} starting with {len(payload)} input keys")

    def on_agent_result(
        self, agent_name: str, chunks: list[dict[str, Any]], latency_ms: float
    ) -> None:
        print(f"✓ {agent_name} completed in {latency_ms:.0f} ms")

    def on_error(self, agent_name: str, error: Exception) -> None:
        print(f"✗ {agent_name} failed: {error}")
```

---

## Package structure

```
my-plugin/
├── pyproject.toml
├── README.md
└── src/
    └── my_plugin/
        ├── __init__.py
        └── plugin.py
```

**`pyproject.toml`:**

```toml
[project]
name = "my-replicate-plugin"
version = "1.0.0"
dependencies = ["replicate-mcp-agents>=0.4.0"]

[project.entry-points."replicate_mcp.plugins"]
my_plugin = "my_plugin.plugin:MyPlugin"
```

---

## Installing and using your plugin

```bash
# Development install
pip install -e .

# Production install from PyPI
pip install my-replicate-plugin
```

Then in your application:

```python
from replicate_mcp.plugins import load_plugins, PluginRegistry

plugins = load_plugins()          # discovers all installed plugins
registry = PluginRegistry()
registry.load_many(plugins)

# Plugins are now active and will receive hooks during execution
```

---

## Testing your plugin

Use `extra_classes` to load your plugin without installing it:

```python
from replicate_mcp.plugins import load_plugins, PluginRegistry
from my_plugin.plugin import MyPlugin

plugins = load_plugins(extra_classes=[MyPlugin])
registry = PluginRegistry()
registry.load_many(plugins)

registry.dispatch_run("test_agent", {"prompt": "hello"})
registry.dispatch_result("test_agent", [{"done": True}], latency_ms=42.0)
registry.unload_all()
```

---

## Hook error handling

**Hooks must never raise exceptions.**  If they do, the `PluginRegistry`
catches the error, logs a warning, and continues dispatching to the remaining
plugins.  Your `teardown()` is similarly guarded — a failing teardown is logged
but does not prevent other plugins from being torn down.

---

## Best practices

- Keep `setup()` fast — it runs at server startup.
- Keep hooks non-blocking — use `asyncio.create_task` for I/O.
- Version-pin `replicate-mcp-agents` with a compatible range, e.g.
  `>=0.4.0,<1.0.0`.
- Export `BasePlugin` and `PluginMetadata` re-exports from your package
  `__init__` for discoverability.