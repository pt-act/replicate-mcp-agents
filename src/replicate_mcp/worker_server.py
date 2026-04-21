"""HTTP worker node server for distributed Replicate agent execution.

Phase 4 — Distributed Execution.  Provides an ASGI application that
exposes a :class:`~replicate_mcp.agents.execution.AgentExecutor` over
HTTP so that :class:`~replicate_mcp.distributed.RemoteWorkerNode` clients
can dispatch tasks to it from a different machine (or process).

Architecture (see ADR-008):
    - :class:`WorkerHttpApp` is a Starlette ASGI application.
    - Each instance wraps a single :class:`AgentExecutor`.
    - Endpoints:
        - ``POST /execute``  — run an agent invocation, return JSON result.
        - ``GET  /health``   — liveness probe (always 200 while running).
        - ``GET  /metrics``  — lightweight load counters.
    - :func:`serve_worker` starts the app with ``uvicorn`` — the
      recommended production runner.

Usage::

    # On the worker machine:
    from replicate_mcp.worker_server import serve_worker
    import asyncio

    asyncio.run(serve_worker(host="0.0.0.0", port=7999))

    # On the coordinator machine:
    from replicate_mcp.distributed import (
        DistributedExecutor, HttpWorkerTransport, RemoteWorkerNode
    )

    transport = HttpWorkerTransport("http://worker-host:7999")
    node = RemoteWorkerNode("worker-1", transport=transport)

    async with DistributedExecutor() as executor:
        executor.add_remote_node(node)
        result = await (await executor.submit("llama_chat", {"prompt": "hi"}))
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from replicate_mcp.agents.execution import AgentExecutor
from replicate_mcp.distributed import TaskResult, TaskStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ASGI application
# ---------------------------------------------------------------------------


class WorkerHttpApp:
    """Starlette ASGI application that exposes a worker node over HTTP.

    Implements the three endpoints expected by
    :class:`~replicate_mcp.distributed.HttpWorkerTransport`:

    * ``POST /execute``  — run an agent, return :class:`TaskResult` JSON.
    * ``GET  /health``   — always returns ``{"status": "healthy", "node_id": ...}``.
    * ``GET  /metrics``  — returns ``{"active_tasks": N, "total_processed": M}``.

    Args:
        executor: :class:`AgentExecutor` instance to run tasks.
        node_id:  Human-readable identifier included in every response.
    """

    def __init__(
        self,
        executor: AgentExecutor | None = None,
        *,
        node_id: str | None = None,
    ) -> None:
        self._executor = executor or AgentExecutor()
        self._node_id = node_id or f"worker-{os.getpid()}"
        self._active_tasks: int = 0
        self._total_processed: int = 0

        # Build Starlette app lazily to avoid import-time side effects
        self._app = self._build_app()

    def _build_app(self) -> Any:  # noqa: ANN401
        from starlette.applications import Starlette  # noqa: PLC0415
        from starlette.requests import Request  # noqa: PLC0415
        from starlette.responses import JSONResponse  # noqa: PLC0415
        from starlette.routing import Route  # noqa: PLC0415

        async def health(request: Request) -> JSONResponse:  # noqa: ARG001
            return JSONResponse(
                {"status": "healthy", "node_id": self._node_id},
                status_code=200,
            )

        async def metrics(request: Request) -> JSONResponse:  # noqa: ARG001
            return JSONResponse(
                {
                    "node_id": self._node_id,
                    "active_tasks": self._active_tasks,
                    "total_processed": self._total_processed,
                }
            )

        async def execute(request: Request) -> JSONResponse:
            try:
                body: dict[str, Any] = await request.json()
            except Exception as exc:  # noqa: BLE001
                return JSONResponse(
                    {"error": f"Invalid JSON body: {exc}"},
                    status_code=400,
                )

            task_id: str = body.get("task_id", "")
            agent_name: str = body.get("agent_name", "")
            payload: dict[str, Any] = body.get("payload", {})

            if not agent_name:
                return JSONResponse(
                    {"error": "'agent_name' is required"},
                    status_code=422,
                )

            self._active_tasks += 1
            t0 = time.perf_counter()
            chunks: list[dict[str, Any]] = []
            error_msg: str | None = None

            try:
                async for chunk in self._executor.run(agent_name, payload):
                    chunks.append(chunk)
                    if chunk.get("error"):
                        error_msg = chunk["error"]
                status = TaskStatus.DONE if not error_msg else TaskStatus.FAILED
                self._total_processed += 1
            except Exception as exc:  # noqa: BLE001
                error_msg = str(exc)
                status = TaskStatus.FAILED
                logger.exception("Task %s failed on worker %s", task_id, self._node_id)
            finally:
                self._active_tasks -= 1

            elapsed_ms = (time.perf_counter() - t0) * 1000

            result = TaskResult(
                task_id=task_id,
                agent_name=agent_name,
                node_id=self._node_id,
                chunks=chunks,
                status=status,
                error=error_msg,
                elapsed_ms=elapsed_ms,
            )
            # Serialize TaskResult as plain dict
            from dataclasses import asdict  # noqa: PLC0415

            return JSONResponse(asdict(result), status_code=200)

        return Starlette(
            routes=[
                Route("/health", health, methods=["GET"]),
                Route("/metrics", metrics, methods=["GET"]),
                Route("/execute", execute, methods=["POST"]),
            ]
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:  # noqa: ANN401
        await self._app(scope, receive, send)

    def __repr__(self) -> str:
        return (
            f"WorkerHttpApp(node_id={self._node_id!r}, "
            f"active={self._active_tasks}, "
            f"processed={self._total_processed})"
        )


# ---------------------------------------------------------------------------
# Convenience server launcher
# ---------------------------------------------------------------------------


async def serve_worker(
    *,
    host: str = "0.0.0.0",  # noqa: S104  # nosec B104
    port: int = 7999,
    api_token: str | None = None,
    node_id: str | None = None,
    log_level: str = "info",
    max_concurrency: int = 8,
) -> None:
    """Launch an HTTP worker node server with ``uvicorn``.

    This is the primary entry point for running a distributed worker.
    Workers can be launched on any machine that has network access to
    the Replicate API.

    Args:
        host:            Network interface to bind (default: all interfaces).
        port:            TCP port to listen on (default: 7999).
        api_token:       Replicate API token; falls back to
                         ``REPLICATE_API_TOKEN`` env var.
        node_id:         Human-readable identifier for this worker node.
        log_level:       Uvicorn log level (``"debug"``, ``"info"``, …).
        max_concurrency: Maximum parallel Replicate API calls per worker.

    Example::

        import asyncio
        from replicate_mcp.worker_server import serve_worker

        asyncio.run(serve_worker(host="0.0.0.0", port=7999))
    """
    try:
        import uvicorn  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "uvicorn is required to run a worker server. " "Install it with: pip install uvicorn"
        ) from exc

    resolved_token = api_token or os.environ.get("REPLICATE_API_TOKEN", "")
    executor = AgentExecutor(
        api_token=resolved_token,
        max_concurrency=max_concurrency,
    )
    app = WorkerHttpApp(executor=executor, node_id=node_id)

    logger.info(
        "Starting HTTP worker node %r on http://%s:%d",
        app._node_id,  # noqa: SLF001
        host,
        port,
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


__all__ = [
    "WorkerHttpApp",
    "serve_worker",
]
