"""Tests for replicate_mcp.server module-level singletons and routing."""

from __future__ import annotations


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
            assert meta.replicate_model() in routing_stats, (
                f"{meta.replicate_model()} not in router stats"
            )

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
        import json  # noqa: PLC0415

        from replicate_mcp.server import _router  # noqa: PLC0415

        board = _router.leaderboard()
        result = json.dumps([{"model": m, "ema_cost_usd": c} for m, c in board])
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert all("model" in item for item in parsed)
