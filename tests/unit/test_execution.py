"""Unit tests for AgentExecutor, ModelCatalogue, and retry/concurrency."""

from __future__ import annotations

import pytest

from replicate_mcp.agents.execution import (
    AgentExecutor,
    DEFAULT_MODEL_MAP,
    ModelCatalogue,
    ModelInfo,
)
from replicate_mcp.exceptions import ModelNotFoundError
from replicate_mcp.resilience import RetryConfig, compute_retry_delay


# -----------------------------------------------------------------------
# ModelInfo
# -----------------------------------------------------------------------


class TestModelInfo:
    def test_basic_construction(self) -> None:
        info = ModelInfo(owner="meta", name="llama-3")
        assert info.owner == "meta"
        assert info.name == "llama-3"
        assert info.description == ""
        assert info.default_input_schema == {}

    def test_with_schema(self) -> None:
        info = ModelInfo(
            owner="stability",
            name="sdxl",
            default_input_schema={"type": "object"},
        )
        assert info.default_input_schema["type"] == "object"


# -----------------------------------------------------------------------
# ModelCatalogue
# -----------------------------------------------------------------------


class TestModelCatalogue:
    def test_empty_catalogue(self) -> None:
        cat = ModelCatalogue()
        assert cat.models == {}

    def test_add_and_get(self) -> None:
        cat = ModelCatalogue()
        cat.add("meta/llama", ModelInfo(owner="meta", name="llama"))
        assert cat.get("meta/llama") is not None
        assert cat.get("meta/llama").name == "llama"

    def test_get_missing_returns_none(self) -> None:
        cat = ModelCatalogue()
        assert cat.get("nonexistent") is None

    def test_is_stale_initially(self) -> None:
        cat = ModelCatalogue()
        assert cat.is_stale() is True

    def test_models_returns_copy(self) -> None:
        cat = ModelCatalogue()
        cat.add("a/b", ModelInfo(owner="a", name="b"))
        copy = cat.models
        copy.clear()
        assert len(cat.models) == 1


# -----------------------------------------------------------------------
# compute_retry_delay (was _decorrelated_jitter)
# -----------------------------------------------------------------------


class TestDecorrelatedJitter:
    def test_returns_non_negative(self) -> None:
        cfg = RetryConfig(base_delay=0.5, max_delay=30.0)
        for attempt in range(10):
            val = compute_retry_delay(attempt, cfg)
            assert val >= 0

    def test_bounded_by_cap(self) -> None:
        cfg = RetryConfig(base_delay=1.0, max_delay=5.0, jitter_factor=0.0)
        for _ in range(50):
            val = compute_retry_delay(100, cfg)
            assert val <= 5.0

    def test_zero_base(self) -> None:
        cfg = RetryConfig(base_delay=0.0, max_delay=10.0, jitter_factor=0.0)
        val = compute_retry_delay(5, cfg)
        assert val == 0.0


# -----------------------------------------------------------------------
# AgentExecutor
# -----------------------------------------------------------------------


class TestAgentExecutor:
    """Tests for AgentExecutor without calling the real Replicate API."""

    def test_resolve_model_short_name(self) -> None:
        executor = AgentExecutor()
        assert executor.resolve_model("llama3_chat") == "meta/meta-llama-3-70b-instruct"

    def test_resolve_model_passthrough(self) -> None:
        executor = AgentExecutor()
        assert executor.resolve_model("meta/llama-3") == "meta/llama-3"

    def test_resolve_model_unknown_raises(self) -> None:
        executor = AgentExecutor()
        with pytest.raises(ModelNotFoundError, match="not found"):
            executor.resolve_model("nonexistent")

    def test_custom_model_map(self) -> None:
        executor = AgentExecutor(model_map={"myagent": "my/model"})
        assert executor.resolve_model("myagent") == "my/model"

    def test_resolve_model_from_catalogue(self) -> None:
        cat = ModelCatalogue()
        cat.add("owner/cool-model", ModelInfo(owner="owner", name="cool-model"))
        executor = AgentExecutor(catalogue=cat)
        assert executor.resolve_model("cool-model") == "owner/cool-model"

    @pytest.mark.asyncio()
    async def test_run_without_token_yields_error(self) -> None:
        executor = AgentExecutor(api_token="")
        results = [chunk async for chunk in executor.run("llama3_chat", {"prompt": "hi"})]
        assert len(results) == 1
        assert results[0]["error"] == "REPLICATE_API_TOKEN is not set"
        assert results[0]["done"] is True

    def test_default_model_map_is_populated(self) -> None:
        assert "llama3_chat" in DEFAULT_MODEL_MAP
        assert "/" in DEFAULT_MODEL_MAP["llama3_chat"]

    def test_catalogue_property(self) -> None:
        executor = AgentExecutor()
        assert isinstance(executor.catalogue, ModelCatalogue)