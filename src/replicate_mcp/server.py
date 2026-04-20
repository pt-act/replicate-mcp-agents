"""MCP server entrypoint for exposing Replicate agents.

This module wires the Replicate agent registry to the Model Context
Protocol so that every registered model appears as an MCP tool in
clients such as Claude Desktop or Cursor.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from replicate_mcp.agents.execution import AgentExecutor
from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry bootstrap — register a default agent so the server always has
# at least one tool available for demonstration purposes.
# ---------------------------------------------------------------------------

_registry = AgentRegistry()
_executor = AgentExecutor()

_registry.register(
    AgentMetadata(
        safe_name="llama3_chat",
        description=(
            "Chat with Meta Llama 3 — a general-purpose large language model. "
            "Provide a 'prompt' string and receive a text completion."
        ),
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
    )
)


def _build_server() -> Any:
    """Construct and return a FastMCP server instance.

    We import ``mcp`` inside this function so that the rest of the
    package can be used without the MCP SDK installed (e.g. for
    tests that don't exercise the server).
    """

    from mcp.server.fastmcp import FastMCP  # type: ignore[import-untyped]

    mcp = FastMCP(
        "Replicate Agent Server",
        version="0.1.0",
    )

    # --- dynamically register every agent as an MCP tool ----------------

    for agent in _registry.get_available_models():

        # Capture *agent* in the closure via a default argument.
        def _make_handler(meta: AgentMetadata):  # noqa: ANN202
            async def _handler(**kwargs: Any) -> str:  # type: ignore[override]
                token = os.environ.get("REPLICATE_API_TOKEN", "")
                if not token:
                    return json.dumps(
                        {"error": "REPLICATE_API_TOKEN environment variable is not set."}
                    )

                results: list[dict[str, Any]] = []
                async for chunk in _executor.run(meta.safe_name, kwargs):
                    results.append(chunk)

                return json.dumps(results, indent=2)

            _handler.__name__ = meta.safe_name
            _handler.__doc__ = meta.description
            return _handler

        handler = _make_handler(agent)
        mcp.tool()(handler)

    return mcp


def serve() -> None:
    """Launch the MCP server over stdio transport.

    This is the entrypoint wired to the ``replicate-mcp-server``
    console script in ``pyproject.toml``.
    """

    mcp = _build_server()
    mcp.run(transport="stdio")


__all__ = ["serve", "_registry", "_executor"]
