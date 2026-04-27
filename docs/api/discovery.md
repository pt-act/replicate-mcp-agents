# `replicate_mcp.discovery` — Dynamic Model Discovery

::: replicate_mcp.discovery
    options:
      members:
        - DiscoveryConfig
        - DiscoveryResult
        - ModelDiscovery
        - discover_and_register

!!! note "ModelCatalogue Deprecation"
    The legacy `ModelCatalogue` class (in `agents/execution.py`) is deprecated
    and will be removed in v0.8.0. Use `ModelDiscovery` for all new code.
    `ModelCatalogue` currently delegates to `ModelDiscovery` internally.

## Quick start

```python
import asyncio
from replicate_mcp.discovery import ModelDiscovery, DiscoveryConfig
from replicate_mcp.agents.registry import AgentRegistry

async def main() -> None:
    config = DiscoveryConfig(owner="meta", max_models=20, ttl_seconds=300)
    registry = AgentRegistry()
    disc = ModelDiscovery(registry=registry, config=config)

    result = await disc.refresh()
    print(f"Discovered {result.discovered} models in {result.elapsed_ms:.0f} ms")

asyncio.run(main())
```

## Background refresh

```python
task = disc.start_background_refresh(api_token="r8_...")
# ... application runs ...
disc.stop_background_refresh()
```

## Filters

| Filter | Config field | Default |
|---|---|---|
| Owner | `owner` | All owners |
| Tags | `required_tags` | All tags |
| Count | `max_models` | 50 |
| TTL | `ttl_seconds` | 300 s |