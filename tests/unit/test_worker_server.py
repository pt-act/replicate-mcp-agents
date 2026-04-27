"""Tests for replicate_mcp.worker_server — HTTP worker node ASGI app."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from replicate_mcp.worker_server import WorkerHttpApp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(chunks: list[dict[str, Any]] | None = None) -> WorkerHttpApp:
    """Build a WorkerHttpApp with a mocked AgentExecutor."""
    mock_executor = MagicMock()

    async def _fake_run(agent_name: str, payload: dict[str, Any]):  # type: ignore[misc]
        for chunk in (chunks or [{"done": True, "output": "hello", "latency_ms": 10.0}]):
            yield chunk

    mock_executor.run = _fake_run
    return WorkerHttpApp(executor=mock_executor, node_id="test-node")


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_200(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_body_has_status_and_node_id(self) -> None:
        app = _make_app()
        client = TestClient(app)
        data = client.get("/health").json()
        assert data["status"] == "healthy"
        assert data["node_id"] == "test-node"


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_metrics_returns_200(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_contains_counters(self) -> None:
        app = _make_app()
        client = TestClient(app)
        data = client.get("/metrics").json()
        assert "active_tasks" in data
        assert "total_processed" in data
        assert data["node_id"] == "test-node"


# ---------------------------------------------------------------------------
# Execute endpoint
# ---------------------------------------------------------------------------


class TestExecuteEndpoint:
    def test_execute_happy_path(self) -> None:
        app = _make_app([{"done": True, "output": "world", "latency_ms": 5.0}])
        client = TestClient(app)
        resp = client.post(
            "/execute",
            json={"task_id": "tid-1", "agent_name": "my_agent", "payload": {"prompt": "hi"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "tid-1"
        assert data["agent_name"] == "my_agent"
        assert data["node_id"] == "test-node"
        assert data["status"] == "done"
        assert len(data["chunks"]) == 1

    def test_execute_increments_total_processed(self) -> None:
        app = _make_app()
        client = TestClient(app)
        client.post("/execute", json={"task_id": "t1", "agent_name": "a", "payload": {}})
        client.post("/execute", json={"task_id": "t2", "agent_name": "a", "payload": {}})
        metrics = client.get("/metrics").json()
        assert metrics["total_processed"] == 2

    def test_execute_missing_agent_name_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/execute", json={"task_id": "t1", "payload": {}})
        assert resp.status_code == 422

    def test_execute_invalid_json_returns_400(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/execute",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_execute_error_chunk_marks_failed(self) -> None:
        app = _make_app([{"error": "boom", "done": True}])
        client = TestClient(app)
        resp = client.post(
            "/execute",
            json={"task_id": "t1", "agent_name": "bad_agent", "payload": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"

    def test_execute_exception_marks_failed(self) -> None:
        """If the executor itself raises, the response should be status=failed."""
        mock_executor = MagicMock()

        async def _raising_run(agent_name: str, payload: dict[str, Any]):  # type: ignore[misc]
            yield {"chunk": "x"}  # makes this an async generator
            raise RuntimeError("executor explosion")

        mock_executor.run = _raising_run
        app = WorkerHttpApp(executor=mock_executor, node_id="node-x")
        client = TestClient(app)
        resp = client.post(
            "/execute",
            json={"task_id": "t1", "agent_name": "boom", "payload": {}},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    def test_repr(self) -> None:
        app = _make_app()
        r = repr(app)
        assert "WorkerHttpApp" in r
        assert "test-node" in r


# ---------------------------------------------------------------------------
# serve_worker
# ---------------------------------------------------------------------------


class TestServeWorker:
    async def test_serve_worker_calls_uvicorn(self) -> None:
        """serve_worker should construct a WorkerHttpApp and start uvicorn."""
        from unittest.mock import MagicMock  # noqa: PLC0415

        from replicate_mcp.worker_server import serve_worker  # noqa: PLC0415

        mock_server = MagicMock()
        mock_server.serve = AsyncMock(return_value=None)
        mock_config = MagicMock()

        with patch("uvicorn.Config", return_value=mock_config) as mock_cfg_cls, \
             patch("uvicorn.Server", return_value=mock_server):
            await serve_worker(
                host="127.0.0.1",
                port=17999,
                api_token="r8_test",  # noqa: S106
                node_id="test-worker",
            )

        mock_cfg_cls.assert_called_once()
        cfg_call = mock_cfg_cls.call_args
        assert cfg_call.kwargs.get("host") == "127.0.0.1"
        assert cfg_call.kwargs.get("port") == 17999
        mock_server.serve.assert_awaited_once()

    async def test_serve_worker_default_node_id_is_set(self) -> None:
        """serve_worker without node_id should auto-generate one from PID."""
        from unittest.mock import MagicMock  # noqa: PLC0415

        from replicate_mcp.worker_server import serve_worker  # noqa: PLC0415

        mock_server = MagicMock()
        mock_server.serve = AsyncMock(return_value=None)

        with patch("uvicorn.Config", return_value=MagicMock()), \
             patch("uvicorn.Server", return_value=mock_server):
            await serve_worker(host="127.0.0.1", port=17998, api_token="r8_x")  # noqa: S106

        # Server.serve was called — no crash with default node_id
        mock_server.serve.assert_awaited_once()
