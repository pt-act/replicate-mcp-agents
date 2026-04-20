"""MCP server entrypoint for exposing Replicate agents.

This module wires the Replicate agent registry to the Model Context
Protocol so that every registered model appears as an MCP tool in
clients such as Claude Desktop or Cursor.

Phase 2 additions:
    - :class:`~replicate_mcp.routing.CostAwareRouter` selects the best
      model when multiple candidates are registered.
    - :class:`~replicate_mcp.validation.AgentMetadataModel` validates
      every agent registration.
    - :class:`~replicate_mcp.observability.Observability` is initialised
      so OTEL spans/metrics flow from every tool invocation.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from replicate_mcp.agents.execution import AgentExecutor
from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry
from replicate_mcp.observability import default_observability
from replicate_mcp.routing import CostAwareRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry bootstrap — register default agents so the server always has
# tools available for demonstration purposes.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 2: Initialise observability and routing at server bootstrap
# ---------------------------------------------------------------------------

# Observability — set up lazily so import side-effects are minimal.
# Call default_observability.setup() here; it no-ops if OTEL SDK absent.
default_observability.setup()

# Cost-aware router — used by the server to select among tagged aliases.
_router = CostAwareRouter(strategy="thompson")

_registry = AgentRegistry()
_executor = AgentExecutor(observability=default_observability)

_DEFAULT_AGENTS: list[AgentMetadata] = [
    AgentMetadata(
        safe_name="llama3_chat",
        description=(
            "Chat with Meta Llama 3 — a general-purpose large language model. "
            "Provide a 'prompt' string and receive a text completion."
        ),
        model="meta/meta-llama-3-70b-instruct",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The text prompt to send to the model.",
                },
            },
            "required": ["prompt"],
        },
        supports_streaming=True,
        estimated_cost=0.002,
        avg_latency_ms=3000,
        tags=["text", "chat"],
    ),
    AgentMetadata(
        safe_name="flux_pro",
        description="Generate images with FLUX 1.1 Pro by Black Forest Labs.",
        model="black-forest-labs/flux-1.1-pro",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the image to generate.",
                },
            },
            "required": ["prompt"],
        },
        supports_streaming=False,
        estimated_cost=0.04,
        avg_latency_ms=8000,
        tags=["image", "generation"],
    ),
]

for _agent in _DEFAULT_AGENTS:
    _registry.register(_agent)
    # Register with the cost-aware router so routing stats accumulate over time
    _router.register_model(
        _agent.replicate_model(),
        initial_cost=_agent.estimated_cost or 0.01,
        initial_latency_ms=float(_agent.avg_latency_ms or 5000),
    )


def _build_server() -> Any:
    """Construct and return a FastMCP server instance.

    We import ``mcp`` inside this function so that the rest of the
    package can be used without the MCP SDK installed (e.g. for
    tests that don't exercise the server).
    """

    from mcp.server.fastmcp import FastMCP  # type: ignore[import-untyped]

    mcp = FastMCP("Replicate Agent Server")

    # --- dynamically register every agent as an MCP tool ----------------

    for _name, meta in _registry.list_agents().items():

        def _make_handler(agent_meta: AgentMetadata) -> Any:
            async def _handler(**kwargs: Any) -> str:  # type: ignore[override]
                token = os.environ.get("REPLICATE_API_TOKEN", "")
                if not token:
                    return json.dumps(
                        {"error": "REPLICATE_API_TOKEN environment variable is not set."}
                    )

                results: list[dict[str, Any]] = []
                async for chunk in _executor.run(agent_meta.safe_name, kwargs):
                    results.append(chunk)

                return json.dumps(results, indent=2)

            _handler.__name__ = agent_meta.safe_name
            _handler.__doc__ = agent_meta.description
            return _handler

        handler = _make_handler(meta)
        mcp.tool()(handler)

    # --- expose a list-models resource ----------------------------------

    @mcp.resource("models://list")
    def _list_models() -> str:
        """List all available Replicate agent models."""
        agents = _registry.list_agents()
        routing_stats = _router.stats()
        return json.dumps(
            {
                name: {
                    "description": a.description,
                    "model": a.replicate_model(),
                    "streaming": a.supports_streaming,
                    "tags": a.tags,
                    "routing": (
                        {
                            "ema_cost_usd": routing_stats[a.replicate_model()].ema_cost_usd,
                            "ema_latency_ms": routing_stats[a.replicate_model()].ema_latency_ms,
                            "success_rate": routing_stats[a.replicate_model()].success_rate,
                            "invocations": routing_stats[a.replicate_model()].invocation_count,
                        }
                        if a.replicate_model() in routing_stats
                        else {}
                    ),
                }
                for name, a in agents.items()
            },
            indent=2,
        )

    @mcp.resource("routing://leaderboard")
    def _routing_leaderboard() -> str:
        """Show model cost leaderboard from the cost-aware router."""
        return json.dumps(
            [{"model": m, "ema_cost_usd": c} for m, c in _router.leaderboard()],
            indent=2,
        )

    return mcp


def serve() -> None:
    """Launch the MCP server over stdio transport.

    This is the entrypoint wired to the ``replicate-mcp-server``
    console script in ``pyproject.toml``.
    """

    mcp = _build_server()
    mcp.run(transport="stdio")


__all__ = ["serve", "_registry", "_executor", "_router"]
