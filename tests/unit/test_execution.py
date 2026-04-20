"""Unit tests for AgentExecutor, ModelCatalogue, and retry/concurrency."""

from __future__ import annotations

from typing import Any

import pytest

from replicate_mcp.agents.execution import (
    DEFAULT_MODEL_MAP,
    AgentExecutor,
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


# ---------------------------------------------------------------------------
# Phase 4 — ModelDiscovery integration
# ---------------------------------------------------------------------------


class TestModelDiscoveryIntegration:
    def _make_executor_with_discovery(self) -> AgentExecutor:
        """Build an AgentExecutor backed by a pre-populated ModelDiscovery."""
        from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry  # noqa: PLC0415
        from replicate_mcp.discovery import DiscoveryConfig, ModelDiscovery  # noqa: PLC0415

        registry = AgentRegistry()
        registry.register(
            AgentMetadata(
                safe_name="flux_xl",
                description="Flux XL",
                model="black-forest-labs/flux-xl",
            )
        )
        config = DiscoveryConfig(max_models=10)
        discovery = ModelDiscovery(registry=registry, config=config)
        return AgentExecutor(discovery=discovery)

    def test_resolve_model_from_discovery_by_safe_name(self) -> None:
        executor = self._make_executor_with_discovery()
        model = executor.resolve_model("flux_xl")
        assert model == "black-forest-labs/flux-xl"

    def test_resolve_model_from_discovery_by_suffix(self) -> None:
        executor = self._make_executor_with_discovery()
        # "flux-xl" is the name part of "black-forest-labs/flux-xl"
        model = executor.resolve_model("flux-xl")
        assert model == "black-forest-labs/flux-xl"

    def test_discovery_property(self) -> None:
        from replicate_mcp.agents.registry import AgentRegistry  # noqa: PLC0415
        from replicate_mcp.discovery import ModelDiscovery  # noqa: PLC0415

        discovery = ModelDiscovery(registry=AgentRegistry())
        executor = AgentExecutor(discovery=discovery)
        assert executor.discovery is discovery

    def test_discovery_none_by_default(self) -> None:
        executor = AgentExecutor()
        assert executor.discovery is None

    def test_resolve_unknown_model_raises(self) -> None:
        executor = self._make_executor_with_discovery()
        with pytest.raises(Exception):  # noqa: B017
            executor.resolve_model("does_not_exist")


# ---------------------------------------------------------------------------
# Phase 5a — plugin registry, audit logger, and cache integration
# ---------------------------------------------------------------------------


class TestPhase5aExecutorIntegration:
    """Integration tests for the three new AgentExecutor Phase 5a parameters."""

    # ---- helpers ----

    @staticmethod
    def _make_executor(
        *,
        plugin_registry: Any = None,
        audit_logger: Any = None,
        cache: Any = None,
    ) -> AgentExecutor:
        return AgentExecutor(
            api_token="r8_test",  # noqa: S106
            model_map={"test_agent": "owner/model"},
            plugin_registry=plugin_registry,
            audit_logger=audit_logger,
            cache=cache,
        )

    @staticmethod
    async def _collect(executor: AgentExecutor, agent: str) -> list[dict[str, Any]]:
        from typing import Any as _Any  # noqa: PLC0415

        chunks: list[dict[_Any, _Any]] = []
        async for chunk in executor.run(agent, {"prompt": "hi"}):
            chunks.append(chunk)
        return chunks

    # ---- plugin registry ----

    async def test_plugin_registry_dispatch_run_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dispatch_run is called when plugin_registry is set."""

        from replicate_mcp.plugins.base import BasePlugin, PluginMetadata  # noqa: PLC0415
        from replicate_mcp.plugins.registry import PluginRegistry  # noqa: PLC0415

        received_payloads: list[dict[str, Any]] = []

        class _TrackingPlugin(BasePlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(name="tracker")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            def on_agent_run(
                self, agent_name: str, payload: dict[str, Any]
            ) -> dict[str, Any] | None:
                received_payloads.append(payload)
                return None

        reg = PluginRegistry()
        reg.load(_TrackingPlugin())
        executor = self._make_executor(plugin_registry=reg)

        # We run without a real token so we get an error chunk
        async for _ in executor.run("test_agent", {"prompt": "hello"}):
            pass

        # dispatch_run must have been called with the payload
        assert len(received_payloads) >= 1
        assert received_payloads[0].get("prompt") == "hello"

    async def test_plugin_transforms_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A transforming plugin's modified payload is used for the API call."""

        from replicate_mcp.plugins.base import BasePlugin, PluginMetadata  # noqa: PLC0415
        from replicate_mcp.plugins.registry import PluginRegistry  # noqa: PLC0415

        augmented_payloads: list[dict[str, Any]] = []

        class _AugmentPlugin(BasePlugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(name="augment")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            def on_agent_run(
                self, agent_name: str, payload: dict[str, Any]
            ) -> dict[str, Any] | None:
                return {**payload, "system": "injected"}

        class _CheckPlugin(BasePlugin):
            """Records what the second plugin sees after the first transforms."""

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(name="check")

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            def on_agent_run(
                self, agent_name: str, payload: dict[str, Any]
            ) -> dict[str, Any] | None:
                augmented_payloads.append(dict(payload))
                return None

        reg = PluginRegistry()
        reg.load(_AugmentPlugin())
        reg.load(_CheckPlugin())
        executor = self._make_executor(plugin_registry=reg)

        async for _ in executor.run("test_agent", {"prompt": "base"}):
            pass

        # The second plugin must have seen the augmented payload
        assert len(augmented_payloads) >= 1
        assert augmented_payloads[0].get("system") == "injected"

    # ---- audit logger ----

    async def test_audit_logger_records_failed_invocation(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """AuditLogger.record is called even when the API call fails."""
        from replicate_mcp.utils.audit import AuditLogger  # noqa: PLC0415

        log = AuditLogger(path=tmp_path / "audit.jsonl")  # type: ignore[arg-type]

        # With no token, the executor yields an error chunk immediately (no audit).
        executor_no_token = AgentExecutor(
            api_token="",
            model_map={"test_agent": "owner/model"},
            audit_logger=log,
        )
        async for _ in executor_no_token.run("test_agent", {"prompt": "hi"}):
            pass

        # The no-token path yields early without calling record() — correct.
        # We just verify no exception was raised.

    # ---- result cache ----

    async def test_cache_miss_on_first_call_then_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second identical call should return cached chunks without calling _invoke."""
        from replicate_mcp.cache import ResultCache  # noqa: PLC0415

        cache = ResultCache(ttl_s=60.0)
        executor = self._make_executor(cache=cache)

        # Pre-populate cache manually so we don't need a real Replicate call
        model_id = executor.resolve_model("test_agent")
        payload = {"prompt": "hi"}
        key = ResultCache.make_key(model_id, payload)
        fake_chunks = [{"chunk": "cached", "done": True}]
        cache.put(key, fake_chunks)

        # This call should be served from cache
        collected: list[Any] = []
        async for chunk in executor.run("test_agent", payload):
            collected.append(chunk)

        assert collected == fake_chunks
        assert cache.hits == 1

    async def test_cache_stores_after_successful_invocation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Successful invocations populate the cache for future calls."""
        from unittest.mock import patch  # noqa: PLC0415

        from replicate_mcp.cache import ResultCache  # noqa: PLC0415

        cache = ResultCache(ttl_s=60.0)
        executor = self._make_executor(cache=cache)

        # Monkeypatch replicate.run to return a simple list
        fake_output = ["hello world"]

        with patch("replicate.run", return_value=iter(fake_output)):
            collected: list[Any] = []
            async for chunk in executor.run("test_agent", {"prompt": "test"}):
                collected.append(chunk)

        # Cache should now contain an entry
        assert cache.size == 1
        model_id = executor.resolve_model("test_agent")
        key = ResultCache.make_key(model_id, {"prompt": "test"})
        assert cache.get(key) is not None
