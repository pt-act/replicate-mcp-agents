# ADR-009 · Latitude Integration for Prompt Management and Tracing

**Status:** Accepted  
**Date:** 2026-04-27  
**Deciders:** Platform Team  
**Scope:** Phase 7 — Integration Ecosystem

---

## Context

As the framework matures, users need:
1. **Versioned prompt management** — prompts should live outside code for rapid iteration
2. **A/B testing** — compare prompt versions with real traffic
3. **Execution tracing** — full observability of agent runs for debugging and optimization
4. **Quality evaluations** — systematic assessment of agent outputs
5. **Training data export** — extract traces for fine-tuning

[Latitude](https://latitude.sh) provides these capabilities as a managed service. This ADR documents the integration design.

## Decision

Implement optional Latitude integration in `replicate_mcp.latitude` with:

### 1. Core Client (`LatitudeClient`)

Async HTTP client implementing Latitude API v3:

**Document API** — Prompt management:
- `get_prompt(path, version_uuid)` — Fetch from specific version (defaults to `"live"`)
- `run_prompt(path, parameters, stream=False)` — Execute prompt via API
- `get_or_create_prompt(path, version_uuid)` — Idempotent fetch/create
- `create_or_update_prompt(path, content, version_uuid)` — Create or overwrite prompt

**Version API** — Prompt versioning:
- `create_version(name)` — Create new draft version
- `publish_version(version_uuid, title, description)` — Publish draft to live

**Conversation API** — Multi-turn chat:
- `chat(conversation_uuid, messages, stream=False)` — Continue conversation
- `get_conversation(conversation_uuid)` — Retrieve conversation history
- `stop_conversation(conversation_uuid)` — Stop active conversation

**Tracing API** — Execution observability:
- `trace()` context manager for execution spans
- `start_trace()` / `end_trace()` for manual control

### 2. Plugin Integration (`LatitudePlugin`)

Automatic tracing via the existing plugin system:
- `on_agent_run()` — start trace with input payload
- `on_agent_result()` — finalize trace with output, latency, cost
- `on_error()` — record error in trace

Zero configuration beyond environment variables (set in your shell):
```bash
# Get API credentials from your Latitude dashboard

# v2 (current): Uses project slug (e.g., "replicate-mcp-agents")
LATITUDE_API_KEY="<your-api-key>"
LATITUDE_PROJECT_SLUG="<your-project-slug>"
export LATITUDE_API_KEY LATITUDE_PROJECT_SLUG

# v1 (legacy): Uses numeric project ID
# LATITUDE_PROJECT_ID="12345"
```

**API Version Support**: The client auto-detects v1 vs v2 based on which project identifier is configured.
- v2: `LATITUDE_PROJECT_SLUG` (e.g., `replicate-mcp-agents`)
- v1: `LATITUDE_PROJECT_ID` (numeric)
- If both set, `project_slug` takes precedence (v2).

### 3. OTEL Bridge (`LatitudeObservabilityBridge`)

Unified telemetry when both Latitude and OpenTelemetry are enabled:
- Single `trace()` call records to both systems
- Correlated span/trace IDs for cross-referencing

## Design Principles

| Principle | Implementation |
|-----------|----------------|
| Zero overhead when disabled | All methods no-op if `LATITUDE_API_KEY` not set |
| Lazy initialization | Client connects on first use, not import |
| Async-first | All I/O is async to match framework architecture |
| Graceful degradation | API failures logged but never break execution |
| Optional dependency | `pip install "replicate-mcp-agents[latitude]"` |

## API Design

```python
# Direct usage
from replicate_mcp.latitude import LatitudeClient, LatitudeConfig

config = LatitudeConfig()  # From env vars
client = LatitudeClient(config)

async with client:
    # Fetch a prompt from live/production
    prompt = await client.get_prompt("system/greeting", version_uuid="live")

    # Execute the prompt via Latitude API
    result = await client.run_prompt(
        "system/greeting",
        parameters={"name": "World"},
        stream=False,
    )

    # Continue the conversation
    chat_result = await client.chat(
        result["uuid"],
        messages=[{"role": "user", "content": [{"type": "text", "text": "Tell me more"}]}]
    )

# Trace an execution
with client.trace("agent-run", agent_id="my-agent") as trace:
    result = await run_agent()
    trace.record_result(result, latency_ms=100, cost_usd=0.001)

# Plugin usage (automatic)
from replicate_mcp.latitude import LatitudePlugin
from replicate_mcp.plugins import PluginRegistry

registry = PluginRegistry()
registry.load(LatitudePlugin(config))
# All agent runs are now traced automatically
```

## Data Model

### `LatitudePrompt`
- `id`, `name` — Document identifier and display name
- `version` — Version UUID
- `content` — Prompt content (PromptL format)
- `config` — Provider parameters (temperature, max_tokens, etc.)
- `metadata` — Provider, model, and other runtime data

### `LatitudePromptResponse` (run_prompt result)
- `uuid` — Conversation UUID for multi-turn
- `response` — Output with text, usage, cost, tool calls
- `tool_requests` — Any tool invocations requested

### `LatitudeTrace`
- `id`, `name`, `agent_id`, `model`
- `input_data`, `output_data`
- `latency_ms`, `cost_usd`, `success`
- `metadata` — custom attributes for querying

### `LatitudeEvalResult`
- `eval_id`, `trace_id`
- `score`, `passed`, `feedback`

## Consequences

### Positive
- Prompts decoupled from code deployments
- A/B testing infrastructure ready
- Centralized observability for all agent executions
- Training data pipeline for fine-tuning

### Negative
- Additional external dependency (Latitude service)
- Potential latency on first prompt fetch (mitigated by caching)
- Another secret to manage (`LATITUDE_API_KEY`)

### Risks & Mitigations
| Risk | Mitigation |
|------|------------|
| API unavailable | Graceful fallback — execution continues, error logged |
| Secret exposure | Uses existing `SecretMasker` patterns |
| Cache staleness | Configurable TTL (default: 5 min) |
| Trace loss on crash | Fire-and-forget async — traces may be lost if process dies mid-send |

## Dependencies

```toml
[project.optional-dependencies]
latitude = ["httpx>=0.27.0,<0.28.0"]
```

`httpx` is already a core dependency, so this is a lightweight addition.

## Related

- ADR-007 Plugin System — lifecycle hooks used for automatic tracing
- ADR-005 Cost-Aware Routing — `cost_usd` field in traces
- `replicate_mcp.observability` — OTEL integration path
