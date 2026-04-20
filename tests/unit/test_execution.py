"""Unit tests for the AgentExecutor."""

from __future__ import annotations

import pytest

from replicate_mcp.agents.execution import AgentExecutor, DEFAULT_MODEL_MAP


class TestAgentExecutor:
    """Tests for AgentExecutor without calling the real Replicate API."""

    def test_resolve_model_short_name(self) -> None:
        executor = AgentExecutor()
        assert executor.resolve_model("llama3_chat") == "meta/meta-llama-3-70b-instruct"

    def test_resolve_model_passthrough(self) -> None:
        """Model IDs containing '/' are returned unchanged."""
        executor = AgentExecutor()
        assert executor.resolve_model("meta/llama-3") == "meta/llama-3"

    def test_resolve_model_unknown_raises(self) -> None:
        executor = AgentExecutor()
        with pytest.raises(KeyError, match="not found"):
            executor.resolve_model("nonexistent")

    def test_custom_model_map(self) -> None:
        executor = AgentExecutor(model_map={"myagent": "my/model"})
        assert executor.resolve_model("myagent") == "my/model"

    @pytest.mark.asyncio()
    async def test_run_without_token_yields_error(self) -> None:
        """When REPLICATE_API_TOKEN is missing, executor yields a structured error."""
        executor = AgentExecutor(api_token="")
        results = [chunk async for chunk in executor.run("llama3_chat", {"prompt": "hi"})]
        assert len(results) == 1
        assert results[0]["error"] == "REPLICATE_API_TOKEN is not set"
        assert results[0]["done"] is True

    def test_default_model_map_is_populated(self) -> None:
        assert "llama3_chat" in DEFAULT_MODEL_MAP
        assert "/" in DEFAULT_MODEL_MAP["llama3_chat"]