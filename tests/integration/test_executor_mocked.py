"""Integration tests for AgentExecutor with mocked Replicate SDK.

These tests verify the full executor flow (resolve → invoke → stream)
without making real API calls.  The ``replicate`` module is imported
lazily inside ``_invoke()``, so we patch ``replicate.run`` at the
library module level.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from replicate_mcp.agents.execution import AgentExecutor


def _install_fake_replicate(run_return=None, run_side_effect=None):
    """Install a fake ``replicate`` module into sys.modules."""
    fake = ModuleType("replicate")
    mock_run = MagicMock()
    if run_side_effect is not None:
        mock_run.side_effect = run_side_effect
    elif run_return is not None:
        mock_run.return_value = run_return
    fake.run = mock_run  # type: ignore
    fake.Client = MagicMock()
    sys.modules["replicate"] = fake
    return mock_run


class TestExecutorMockedStreaming:
    """Test streaming model output."""

    @pytest.mark.asyncio()
    async def test_streaming_text_model(self) -> None:
        """Verify that iterable output is treated as streaming."""
        mock_run = _install_fake_replicate(run_return=iter(["Hello", " ", "world", "!"]))
        try:
            executor = AgentExecutor(api_token="test-token")
            chunks = [c async for c in executor.run("llama3_chat", {"prompt": "hi"})]
        finally:
            sys.modules.pop("replicate", None)

        # 4 streaming chunks + 1 final
        assert len(chunks) == 5
        for c in chunks[:4]:
            assert c["done"] is False
            assert "chunk" in c
        assert chunks[4]["done"] is True
        assert chunks[4]["output"] == "Hello world!"
        assert "latency_ms" in chunks[4]

    @pytest.mark.asyncio()
    async def test_non_streaming_output(self) -> None:
        """Verify string output is yielded as a single chunk."""
        _install_fake_replicate(run_return="direct result")
        try:
            executor = AgentExecutor(api_token="test-token")
            chunks = [c async for c in executor.run("llama3_chat", {"prompt": "hi"})]
        finally:
            sys.modules.pop("replicate", None)

        assert len(chunks) == 1
        assert chunks[0]["done"] is True
        assert chunks[0]["output"] == "direct result"


class TestExecutorMockedErrors:
    """Test error handling and retry behaviour."""

    @pytest.mark.asyncio()
    async def test_api_error_exhausts_retries(self) -> None:
        """Verify that persistent errors exhaust retries and yield error."""
        call_count = 0

        def _always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("persistent failure")

        _install_fake_replicate(run_side_effect=_always_fail)
        try:
            executor = AgentExecutor(
                api_token="test-token",
                max_retries=2,
                retry_base=0.001,  # tiny delay for test speed
            )
            chunks = [c async for c in executor.run("llama3_chat", {"prompt": "hi"})]
        finally:
            sys.modules.pop("replicate", None)

        # Should have tried 3 times (1 initial + 2 retries)
        assert call_count == 3
        # Final chunk should be an error
        assert chunks[-1]["done"] is True
        assert "error" in chunks[-1]

    @pytest.mark.asyncio()
    async def test_model_passthrough_for_full_id(self) -> None:
        """Full model IDs (with /) should be used as-is."""
        mock_run = _install_fake_replicate(run_return="ok")
        try:
            executor = AgentExecutor(api_token="test-token")
            chunks = [
                c async for c in executor.run("custom/model-v2", {"prompt": "test"})
            ]
        finally:
            sys.modules.pop("replicate", None)

        mock_run.assert_called_with("custom/model-v2", input={"prompt": "test"})
        assert chunks[0]["output"] == "ok"


class TestExecutorMockedWorkflow:
    """End-to-end workflow with mocked executor."""

    @pytest.mark.asyncio()
    async def test_workflow_with_mocked_executor(self) -> None:
        """Run a full workflow DAG with a mocked executor."""
        from replicate_mcp.agents.composition import AgentNode, AgentWorkflow, WorkflowEdge

        wf = (
            AgentWorkflow(name="e2e", description="end to end")
            .add_agent("a", AgentNode(model_id="m/a", role="r"))
            .add_agent("b", AgentNode(model_id="m/b", role="r"))
        )
        wf.add_edge(WorkflowEdge(from_agent="a", to_agent="b"))

        class MockExecutor:
            async def run(self, agent_id: str, payload: dict) -> Any:
                yield {"agent": agent_id, "output": f"result-{agent_id}", "done": True}

        results = [
            chunk async for chunk in wf.execute(
                {"seed": 1},
                executor=MockExecutor(),
            )
        ]
        assert len(results) == 2
        assert results[0]["node"] == "a"
        assert results[1]["node"] == "b"