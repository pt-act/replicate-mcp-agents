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
