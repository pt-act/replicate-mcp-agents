"""Tests for replicate_mcp.server module-level singletons and routing."""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestServerModuleSingletons:
    """Test the module-level objects without invoking the MCP server."""

    def test_registry_has_default_agents(self) -> None:
        from replicate_mcp.server import _registry

        agents = _registry.list_agents()
        assert "llama3_chat" in agents
        assert "flux_pro" in agents

    def test_executor_created(self) -> None:
        from replicate_mcp.agents.execution import AgentExecutor
        from replicate_mcp.server import _executor

        assert isinstance(_executor, AgentExecutor)

    def test_router_created(self) -> None:
        from replicate_mcp.routing import CostAwareRouter
        from replicate_mcp.server import _router

        assert isinstance(_router, CostAwareRouter)

    def test_router_has_default_models(self) -> None:
        from replicate_mcp.server import _registry, _router

        # Every registered agent's model should be in the router
        agents = _registry.list_agents()
        routing_stats = _router.stats()
        for _name, meta in agents.items():
            assert (
                meta.replicate_model() in routing_stats
            ), f"{meta.replicate_model()} not in router stats"

    def test_default_agent_metadata(self) -> None:
        from replicate_mcp.server import _registry

        llama = _registry.get("llama3_chat")
        assert llama.model == "meta/meta-llama-3-70b-instruct"
        assert llama.supports_streaming is True
        assert "text" in llama.tags

    def test_flux_agent_metadata(self) -> None:
        from replicate_mcp.server import _registry

        flux = _registry.get("flux_pro")
        assert "flux" in flux.model.lower() or "black-forest" in flux.model.lower()
        assert "image" in flux.tags

    def test_observability_setup_called(self) -> None:
        # Importing server triggers setup() — verify it didn't crash
        from replicate_mcp import server  # noqa: F401
        from replicate_mcp.observability import default_observability

        # setup() may be a no-op if OTEL not installed — just verify no crash
        assert isinstance(default_observability.is_setup, bool)

    def test_executor_resolves_default_model(self) -> None:
        from replicate_mcp.server import _executor

        model = _executor.resolve_model("llama3_chat")
        assert "/" in model

    def test_router_leaderboard_not_empty(self) -> None:
        from replicate_mcp.server import _router

        board = _router.leaderboard()
        assert len(board) >= 2
        # Sorted by cost (ascending)
        costs = [c for _, c in board]
        assert costs == sorted(costs)


# ---------------------------------------------------------------------------
# Phase 4 — HTTP transport functions
# ---------------------------------------------------------------------------


class TestHttpTransportFunctions:
    def test_get_asgi_app_sse_returns_starlette(self) -> None:
        from starlette.applications import Starlette  # noqa: PLC0415

        from replicate_mcp.server import get_asgi_app  # noqa: PLC0415

        app = get_asgi_app(transport="sse")
        assert isinstance(app, Starlette)

    def test_get_asgi_app_streamable_http_returns_starlette(self) -> None:
        from starlette.applications import Starlette  # noqa: PLC0415

        from replicate_mcp.server import get_asgi_app  # noqa: PLC0415

        app = get_asgi_app(transport="streamable-http")
        assert isinstance(app, Starlette)

    def test_get_asgi_app_with_mount_path(self) -> None:
        from replicate_mcp.server import get_asgi_app  # noqa: PLC0415

        # Should not raise when mount_path is supplied
        app = get_asgi_app(transport="sse", mount_path="/mcp")
        assert app is not None

    def test_serve_http_is_callable(self) -> None:
        from replicate_mcp.server import serve_http, serve_streamable_http  # noqa: PLC0415

        assert callable(serve_http)
        assert callable(serve_streamable_http)

    def test_serve_http_invokes_uvicorn_with_correct_args(self) -> None:
        """serve_http should call uvicorn.run with the expected host and port."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        from replicate_mcp.server import serve_http  # noqa: PLC0415

        mock_run = MagicMock()
        with patch("uvicorn.run", mock_run):
            serve_http(host="127.0.0.1", port=18080)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("host") == "127.0.0.1"
        assert call_kwargs.kwargs.get("port") == 18080

    def test_serve_streamable_http_invokes_uvicorn(self) -> None:
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        from replicate_mcp.server import serve_streamable_http  # noqa: PLC0415

        mock_run = MagicMock()
        with patch("uvicorn.run", mock_run):
            serve_streamable_http(host="127.0.0.1", port=18081)

        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs.get("port") == 18081


# ---------------------------------------------------------------------------
# Phase 4 — MCP resource handlers (_build_server internals)
# ---------------------------------------------------------------------------


class TestBuildServerResources:
    """Test that _build_server wires resources that return valid JSON."""

    def test_build_server_creates_valid_mcp_server(self) -> None:
        from replicate_mcp.server import _build_server  # noqa: PLC0415

        server = _build_server()
        assert server is not None

    def test_list_models_resource_json(self) -> None:
        """models://list should return valid JSON with registered agents."""
        import json  # noqa: PLC0415

        from replicate_mcp.server import _registry  # noqa: PLC0415

        agents = _registry.list_agents()
        # Manually call the resource function by recreating its logic
        result = json.dumps(
            {
                name: {
                    "description": a.description,
                    "model": a.replicate_model(),
                    "streaming": a.supports_streaming,
                    "tags": a.tags,
                }
                for name, a in agents.items()
            }
        )
        parsed = json.loads(result)
        assert "llama3_chat" in parsed
        assert "flux_pro" in parsed

    def test_routing_leaderboard_resource_json(self) -> None:
        """routing://leaderboard should return a sorted JSON list."""

        from replicate_mcp.server import _router  # noqa: PLC0415

        board = _router.leaderboard()
        result = json.dumps([{"model": m, "ema_cost_usd": c} for m, c in board])
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert all("model" in item for item in parsed)


# ---------------------------------------------------------------------------
# MCP tool handler — _handler inside _make_handler (lines 121-131)
# ---------------------------------------------------------------------------


class TestMCPToolHandler:
    """Cover the async _handler that runs inside _build_server."""

    @pytest.mark.asyncio
    async def test_handler_no_api_token(self) -> None:
        """When REPLICATE_API_TOKEN is missing, handler returns error JSON."""
        from replicate_mcp.server import _build_server  # noqa: PLC0415

        mcp_server = _build_server()
        # Extract the registered tool handler for llama3_chat
        tools = mcp_server._tool_manager._tools  # type: ignore[attr-defined]
        handler_fn = tools["llama3_chat"].fn  # type: ignore[attr-defined]

        with patch.dict(os.environ, {}, clear=True):
            result = await handler_fn(prompt="hello")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "REPLICATE_API_TOKEN" in parsed["error"]

    @pytest.mark.asyncio
    async def test_handler_with_api_token(self) -> None:
        """When REPLICATE_API_TOKEN is set, handler runs the executor."""
        from replicate_mcp.server import _build_server, _executor  # noqa: PLC0415

        mcp_server = _build_server()
        tools = mcp_server._tool_manager._tools  # type: ignore[attr-defined]
        handler_fn = tools["llama3_chat"].fn  # type: ignore[attr-defined]

        async def _fake_run(*args: Any, **kwargs: Any) -> Any:
            yield {"done": True, "output": "hello world"}

        with patch.dict(os.environ, {"REPLICATE_API_TOKEN": "test-token"}):  # type: ignore[dict-item]
            with patch.object(_executor, "run", _fake_run):
                result = await handler_fn(prompt="hello")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert parsed[0]["done"] is True


# ---------------------------------------------------------------------------
# _list_models resource with routing stats (lines 145-147)
# ---------------------------------------------------------------------------


class TestListModelsResourceWithRouting:
    """Cover the _list_models resource handler registered inside _build_server."""

    def test_list_models_includes_routing_stats(self) -> None:
        """models://list resource includes routing stats for registered models."""
        from replicate_mcp.server import _build_server  # noqa: PLC0415

        mcp_server = _build_server()
        list_models_fn = mcp_server._resource_manager._resources["models://list"].fn  # type: ignore[attr-defined]
        result = list_models_fn()
        parsed = json.loads(result)
        # Default agents should have routing stats populated
        llama = parsed["llama3_chat"]
        assert "routing" in llama
        assert "ema_cost_usd" in llama["routing"]
        assert "ema_latency_ms" in llama["routing"]
        assert "success_rate" in llama["routing"]
        assert "invocations" in llama["routing"]

    def test_list_models_routing_empty_for_unknown(self) -> None:
        """When a model has no routing stats, routing dict is empty."""
        from replicate_mcp.agents.registry import AgentMetadata  # noqa: PLC0415
        from replicate_mcp.server import _registry  # noqa: PLC0415

        # Add a temporary agent NOT registered with the router
        temp_agent = AgentMetadata(
            safe_name="temp_no_route",
            description="Temporary test agent",
            model="test/unregistered-model",
            input_schema={"type": "object", "properties": {}},
            supports_streaming=False,
            estimated_cost=0.01,
            avg_latency_ms=1000,
            tags=["test"],
        )
        _registry.register(temp_agent)
        try:
            from replicate_mcp.server import _build_server  # noqa: PLC0415

            mcp_server = _build_server()
            list_models_fn = mcp_server._resource_manager._resources["models://list"].fn  # type: ignore[attr-defined]
            result = list_models_fn()
            parsed = json.loads(result)
            assert parsed["temp_no_route"]["routing"] == {}
        finally:
            # Clean up so other tests aren't affected
            _registry._agents.pop("temp_no_route", None)  # type: ignore[attr-defined]

    def test_routing_leaderboard_resource_handler(self) -> None:
        """routing://leaderboard resource returns valid JSON via _build_server."""
        from replicate_mcp.server import _build_server  # noqa: PLC0415

        mcp_server = _build_server()
        leaderboard_fn = mcp_server._resource_manager._resources["routing://leaderboard"].fn  # type: ignore[attr-defined]
        result = leaderboard_fn()
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert all("model" in item for item in parsed)


# ---------------------------------------------------------------------------
# serve() function (line 173)
# ---------------------------------------------------------------------------


class TestServe:
    def test_serve_calls_run_with_stdio(self) -> None:
        """serve() should build an MCP server and call run(transport='stdio')."""
        from replicate_mcp.server import serve  # noqa: PLC0415

        mock_mcp = MagicMock()
        with patch("replicate_mcp.server._build_server", return_value=mock_mcp):
            serve()
        mock_mcp.run.assert_called_once_with(transport="stdio")


# ---------------------------------------------------------------------------
# serve_http ImportError branch (lines 188-189)
# ---------------------------------------------------------------------------


class TestServeHttpImportError:
    def test_serve_http_raises_when_uvicorn_missing(self) -> None:
        """serve_http raises ImportError if uvicorn is not installed."""
        from replicate_mcp.server import serve_http  # noqa: PLC0415

        with patch.dict(sys.modules, {"uvicorn": None}):
            with pytest.raises(ImportError, match="uvicorn is required"):
                serve_http()


# ---------------------------------------------------------------------------
# serve_streamable_http ImportError branch (lines 217-218)
# ---------------------------------------------------------------------------


class TestServeStreamableHttpImportError:
    def test_serve_streamable_http_raises_when_uvicorn_missing(self) -> None:
        """serve_streamable_http raises ImportError if uvicorn is not installed."""
        from replicate_mcp.server import serve_streamable_http  # noqa: PLC0415

        with patch.dict(sys.modules, {"uvicorn": None}):
            with pytest.raises(ImportError, match="uvicorn is required"):
                serve_streamable_http()


# ---------------------------------------------------------------------------
# get_asgi_app streamable-http branch (lines 247-248)
# ---------------------------------------------------------------------------


class TestGetAsgiAppStreamableHttp:
    def test_get_asgi_app_streamable_http_calls_streamable_http_app(self) -> None:
        """get_asgi_app with transport='streamable-http' uses streamable_http_app()."""
        from replicate_mcp.server import get_asgi_app  # noqa: PLC0415

        mock_mcp = MagicMock()
        mock_app = MagicMock()
        mock_mcp.streamable_http_app.return_value = mock_app
        with patch("replicate_mcp.server._build_server", return_value=mock_mcp):
            result = get_asgi_app(transport="streamable-http")
        mock_mcp.streamable_http_app.assert_called_once()
        assert result is mock_app

    def test_get_asgi_app_sse_calls_sse_app(self) -> None:
        """get_asgi_app with transport='sse' uses sse_app()."""
        from replicate_mcp.server import get_asgi_app  # noqa: PLC0415

        mock_mcp = MagicMock()
        mock_app = MagicMock()
        mock_mcp.sse_app.return_value = mock_app
        with patch("replicate_mcp.server._build_server", return_value=mock_mcp):
            result = get_asgi_app(transport="sse", mount_path="/mcp")
        mock_mcp.sse_app.assert_called_once_with(mount_path="/mcp")
        assert result is mock_app
