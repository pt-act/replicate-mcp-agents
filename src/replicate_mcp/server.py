"""MCP server entrypoint for exposing Replicate agents."""

from __future__ import annotations

import anyio


async def _serve() -> None:
    """Start the MCP server.

    This placeholder bootstraps the asynchronous event loop until the
    server implementation lands in subsequent commits.
    """

    await anyio.sleep(0)  # noqa: ASYNC115 - placeholder until server implementation


def serve() -> None:
    """Synchronous wrapper that launches the asynchronous server."""

    anyio.run(_serve)


__all__ = ["serve"]
