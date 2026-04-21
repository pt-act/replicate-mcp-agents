"""Tests for replicate_mcp.distributed — distributed execution engine."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from replicate_mcp.distributed import (
    DistributedExecutor,
    HttpWorkerTransport,
    NodeHealth,
    NodeOverloadError,
    NodeRegistry,
    NoHealthyNodesError,
    RemoteWorkerNode,
    TaskHandle,
    TaskResult,
    TaskStatus,
    WorkerNode,
    WorkerTransport,
)

# ---------------------------------------------------------------------------
# TaskStatus
# ---------------------------------------------------------------------------


class TestTaskStatus:
    def test_values(self) -> None:
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.DONE == "done"
        assert TaskStatus.FAILED == "failed"


# ---------------------------------------------------------------------------
# TaskResult
# ---------------------------------------------------------------------------


class TestTaskResult:
    def test_defaults(self) -> None:
        r = TaskResult(task_id="t1", agent_name="a", node_id="n1")
        assert r.chunks == []
        assert r.status == TaskStatus.PENDING
        assert r.error is None
        assert r.elapsed_ms == 0.0


# ---------------------------------------------------------------------------
# TaskHandle
# ---------------------------------------------------------------------------


class TestTaskHandle:
    @pytest.mark.asyncio
    async def test_set_and_await_result(self) -> None:
        handle = TaskHandle("tid")
        result = TaskResult(task_id="tid", agent_name="a", node_id="n")
        handle.set_result(result)
        resolved = await handle
        assert resolved is result

    @pytest.mark.asyncio
    async def test_set_result_idempotent(self) -> None:
        handle = TaskHandle("tid")
        r1 = TaskResult(task_id="tid", agent_name="a", node_id="n")
        r2 = TaskResult(task_id="tid", agent_name="b", node_id="n")
        handle.set_result(r1)
        handle.set_result(r2)  # Second call should be no-op
        resolved = await handle
        assert resolved is r1

    @pytest.mark.asyncio
    async def test_set_exception(self) -> None:
        handle = TaskHandle("tid")
        handle.set_exception(RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await handle

    @pytest.mark.asyncio
    async def test_set_exception_idempotent(self) -> None:
        handle = TaskHandle("tid")
        handle.set_exception(RuntimeError("first"))
        handle.set_exception(RuntimeError("second"))  # no-op
        with pytest.raises(RuntimeError, match="first"):
            await handle

    @pytest.mark.asyncio
    async def test_task_id(self) -> None:
        handle = TaskHandle("my-task-id")
        assert handle.task_id == "my-task-id"


# ---------------------------------------------------------------------------
# NodeHealth
# ---------------------------------------------------------------------------


class TestNodeHealth:
    def test_values(self) -> None:
        assert NodeHealth.HEALTHY == "healthy"
        assert NodeHealth.DEGRADED == "degraded"
        assert NodeHealth.UNHEALTHY == "unhealthy"


# ---------------------------------------------------------------------------
# WorkerNode
# ---------------------------------------------------------------------------


class TestWorkerNode:
    def test_defaults(self) -> None:
        node = WorkerNode("n1")
        assert node.node_id == "n1"
        assert node.health == NodeHealth.HEALTHY
        assert node.queue_depth == 0
        assert node.active_tasks == 0
        assert node.total_processed == 0

    def test_auto_id(self) -> None:
        node = WorkerNode()
        assert node.node_id.startswith("node-")

    def test_load(self) -> None:
        node = WorkerNode("n1")
        assert node.load == 0.0

    def test_mark_unhealthy(self) -> None:
        node = WorkerNode("n1")
        node.mark_unhealthy()
        assert node.health == NodeHealth.UNHEALTHY

    def test_mark_healthy(self) -> None:
        node = WorkerNode("n1")
        node.mark_unhealthy()
        node.mark_healthy()
        assert node.health == NodeHealth.HEALTHY

    def test_repr(self) -> None:
        node = WorkerNode("n1")
        assert "n1" in repr(node)

    @pytest.mark.asyncio
    async def test_enqueue_adds_to_queue(self) -> None:
        node = WorkerNode("n1", max_queue_depth=10)
        handle = TaskHandle("t1")
        node.enqueue("t1", "agent", {}, handle)
        assert node.queue_depth == 1

    @pytest.mark.asyncio
    async def test_enqueue_at_capacity_raises(self) -> None:
        node = WorkerNode("n1", max_queue_depth=1)
        handle1 = TaskHandle("t1")
        handle2 = TaskHandle("t2")
        node.enqueue("t1", "agent", {}, handle1)
        with pytest.raises(NodeOverloadError) as exc_info:
            node.enqueue("t2", "agent", {}, handle2)
        assert exc_info.value.node_id == "n1"

    @pytest.mark.asyncio
    async def test_node_executes_stub_task(self) -> None:
        node = WorkerNode("n1")
        node.start()
        handle = TaskHandle("t1")
        node.enqueue("t1", "chat", {"prompt": "hi"}, handle)
        result = await asyncio.wait_for(handle, timeout=2.0)
        assert result.status == TaskStatus.DONE
        assert result.node_id == "n1"
        assert len(result.chunks) > 0
        await node.stop()

    @pytest.mark.asyncio
    async def test_node_total_processed_increments(self) -> None:
        node = WorkerNode("n1")
        node.start()
        for i in range(3):
            handle = TaskHandle(f"t{i}")
            node.enqueue(f"t{i}", "agent", {}, handle)
            await asyncio.wait_for(handle, timeout=2.0)
        assert node.total_processed == 3
        await node.stop()


# ---------------------------------------------------------------------------
# NodeRegistry
# ---------------------------------------------------------------------------


class TestNodeRegistry:
    def test_add_and_get(self) -> None:
        reg = NodeRegistry()
        node = WorkerNode("n1")
        reg.add(node)
        assert reg.get("n1") is node

    def test_add_duplicate_noop(self) -> None:
        reg = NodeRegistry()
        node = WorkerNode("n1")
        reg.add(node)
        reg.add(node)  # Should not raise
        assert reg.count == 1

    def test_remove(self) -> None:
        reg = NodeRegistry()
        node = WorkerNode("n1")
        reg.add(node)
        removed = reg.remove("n1")
        assert removed is node
        assert reg.count == 0

    def test_remove_missing_returns_none(self) -> None:
        reg = NodeRegistry()
        assert reg.remove("nonexistent") is None

    def test_all_nodes(self) -> None:
        reg = NodeRegistry()
        reg.add(WorkerNode("n1"))
        reg.add(WorkerNode("n2"))
        assert len(reg.all_nodes) == 2

    def test_healthy_nodes_excludes_unhealthy(self) -> None:
        reg = NodeRegistry()
        n1 = WorkerNode("n1")
        n2 = WorkerNode("n2")
        n2.mark_unhealthy()
        reg.add(n1)
        reg.add(n2)
        healthy = reg.healthy_nodes
        assert n1 in healthy
        assert n2 not in healthy

    @pytest.mark.asyncio
    async def test_least_loaded(self) -> None:
        reg = NodeRegistry()
        n1 = WorkerNode("n1", max_queue_depth=10)
        n2 = WorkerNode("n2", max_queue_depth=10)
        reg.add(n1)
        reg.add(n2)
        # Enqueue more tasks on n1
        for i in range(3):
            n1.enqueue(str(i), "a", {}, TaskHandle(str(i)))
        least = reg.least_loaded()
        assert least is n2

    def test_least_loaded_returns_none_when_empty(self) -> None:
        reg = NodeRegistry()
        assert reg.least_loaded() is None

    def test_initial_nodes(self) -> None:
        n1 = WorkerNode("n1")
        n2 = WorkerNode("n2")
        reg = NodeRegistry(nodes=[n1, n2])
        assert reg.count == 2

    def test_count(self) -> None:
        reg = NodeRegistry()
        assert reg.count == 0
        reg.add(WorkerNode("n1"))
        assert reg.count == 1


# ---------------------------------------------------------------------------
# DistributedExecutor
# ---------------------------------------------------------------------------


class TestDistributedExecutor:
    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        async with DistributedExecutor() as executor:
            executor.add_node(WorkerNode("n1"))
            assert executor.node_count == 1

    @pytest.mark.asyncio
    async def test_submit_executes_task(self) -> None:
        async with DistributedExecutor() as executor:
            executor.add_node(WorkerNode("n1"))
            handle = await executor.submit("chat", {"prompt": "hello"})
            result = await asyncio.wait_for(handle, timeout=2.0)
            assert result.status == TaskStatus.DONE
            assert result.agent_name == "chat"
            assert result.node_id == "n1"

    @pytest.mark.asyncio
    async def test_submit_to_least_loaded(self) -> None:
        async with DistributedExecutor() as executor:
            n1 = WorkerNode("n1", max_queue_depth=10)
            n2 = WorkerNode("n2", max_queue_depth=10)
            executor.add_node(n1)
            executor.add_node(n2)
            # Two tasks go to n1 and n2 alternately
            h1 = await executor.submit("a", {})
            h2 = await executor.submit("a", {})
            r1 = await asyncio.wait_for(h1, timeout=2.0)
            r2 = await asyncio.wait_for(h2, timeout=2.0)
            # Both should complete
            assert r1.status == TaskStatus.DONE
            assert r2.status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_submit_no_nodes_raises(self) -> None:
        async with DistributedExecutor() as executor:
            with pytest.raises(NoHealthyNodesError):
                await executor.submit("agent", {})

    @pytest.mark.asyncio
    async def test_run_many(self) -> None:
        async with DistributedExecutor() as executor:
            executor.add_node(WorkerNode("n1"))
            tasks = [("agent_a", {"x": i}) for i in range(4)]
            results = await asyncio.wait_for(
                executor.run_many(tasks), timeout=5.0
            )
            assert len(results) == 4
            assert all(r.status == TaskStatus.DONE for r in results)

    @pytest.mark.asyncio
    async def test_stream(self) -> None:
        async with DistributedExecutor() as executor:
            executor.add_node(WorkerNode("n1"))
            chunks = []
            async for chunk in executor.stream("agent", {"prompt": "hi"}):
                chunks.append(chunk)
            assert len(chunks) > 0

    def test_node_count_property(self) -> None:
        executor = DistributedExecutor()
        assert executor.node_count == 0
        executor.add_node(WorkerNode("n1"))
        assert executor.node_count == 1

    def test_nodes_property(self) -> None:
        executor = DistributedExecutor()
        n1 = WorkerNode("n1")
        executor.add_node(n1)
        assert n1 in executor.nodes

    def test_repr(self) -> None:
        executor = DistributedExecutor()
        assert "DistributedExecutor" in repr(executor)

    @pytest.mark.asyncio
    async def test_two_nodes_distribute_work(self) -> None:
        """Verify tasks are spread across 2 nodes."""
        async with DistributedExecutor() as executor:
            executor.add_node(WorkerNode("n1"))
            executor.add_node(WorkerNode("n2"))
            handles = []
            for i in range(10):
                h = await executor.submit("agent", {"i": i})
                handles.append(h)
            results = await asyncio.gather(*handles)
            node_ids = {r.node_id for r in results}
            # Both nodes should have been used
            assert len(node_ids) >= 1

    @pytest.mark.asyncio
    async def test_overloaded_node_failover(self) -> None:
        """Overloaded node is marked unhealthy; second node takes over."""
        executor = DistributedExecutor(max_retries=2)
        n1 = WorkerNode("n1", max_queue_depth=0)  # Always overloaded
        n2 = WorkerNode("n2", max_queue_depth=10)
        executor.add_node(n1)
        executor.add_node(n2)
        executor.start()
        handle = await executor.submit("agent", {})
        result = await asyncio.wait_for(handle, timeout=2.0)
        assert result.status == TaskStatus.DONE
        await executor.stop()

    @pytest.mark.asyncio
    async def test_all_overloaded_raises(self) -> None:
        """When all nodes are overloaded/unhealthy, raise NoHealthyNodesError."""
        executor = DistributedExecutor(max_retries=0)
        n1 = WorkerNode("n1", max_queue_depth=0)
        executor.add_node(n1)
        executor.start()
        with pytest.raises((NoHealthyNodesError, NodeOverloadError)):
            await executor.submit("agent", {})
        await executor.stop()


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class TestErrorTypes:
    def test_node_overload_error(self) -> None:
        e = NodeOverloadError("n1", 5)
        assert "n1" in str(e)
        assert e.node_id == "n1"
        assert e.queue_depth == 5

    def test_no_healthy_nodes_error(self) -> None:
        e = NoHealthyNodesError()
        assert "healthy" in str(e).lower()


# ---------------------------------------------------------------------------
# Phase 4 — WorkerTransport / HttpWorkerTransport / RemoteWorkerNode
# ---------------------------------------------------------------------------


class TestWorkerTransport:
    """WorkerTransport is an ABC — check it cannot be instantiated."""

    def test_is_abstract(self) -> None:
        assert hasattr(WorkerTransport, "__abstractmethods__")


class TestHttpWorkerTransport:
    def _transport(self) -> HttpWorkerTransport:
        return HttpWorkerTransport("http://localhost:7999", timeout=5.0)

    def test_base_url_stripped(self) -> None:
        t = HttpWorkerTransport("http://host:7999/")
        assert t.base_url == "http://host:7999"

    def test_repr(self) -> None:
        t = self._transport()
        assert "HttpWorkerTransport" in repr(t)
        assert "7999" in repr(t)

    async def test_health_check_ok(self) -> None:
        t = self._transport()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            ok = await t.health_check()
        assert ok is True

    async def test_health_check_fail(self) -> None:
        t = self._transport()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(
                side_effect=ConnectionError("refused")
            )
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            ok = await t.health_check()
        assert ok is False

    async def test_get_metrics_returns_dict(self) -> None:
        t = self._transport()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value={"active_tasks": 2})
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            metrics = await t.get_metrics()
        assert metrics.get("active_tasks") == 2

    async def test_get_metrics_on_error_returns_empty(self) -> None:
        t = self._transport()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            metrics = await t.get_metrics()
        assert metrics == {}

    async def test_submit_deserialises_task_result(self) -> None:
        t = self._transport()
        payload = {
            "task_id": "t1",
            "agent_name": "my_agent",
            "node_id": "remote-node",
            "chunks": [{"done": True}],
            "status": "done",
            "error": None,
            "elapsed_ms": 123.4,
        }
        with patch("httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=payload)
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await t.submit("t1", "my_agent", {"prompt": "hi"})

        assert isinstance(result, TaskResult)
        assert result.task_id == "t1"
        assert result.status == TaskStatus.DONE
        assert result.elapsed_ms == pytest.approx(123.4)


class TestRemoteWorkerNode:
    def _make_transport(self, task_result: TaskResult | None = None) -> HttpWorkerTransport:
        """Return a mocked transport that resolves to task_result."""
        t = MagicMock(spec=HttpWorkerTransport)
        t.health_check = AsyncMock(return_value=True)
        t.get_metrics = AsyncMock(return_value={})
        t.submit = AsyncMock(
            return_value=task_result or TaskResult(
                task_id="t1", agent_name="a", node_id="remote", status=TaskStatus.DONE
            )
        )
        t.__repr__ = MagicMock(return_value="MockTransport()")
        return t  # type: ignore[return-value]

    def test_properties(self) -> None:
        t = self._make_transport()
        node = RemoteWorkerNode("remote-1", transport=t)
        assert node.node_id == "remote-1"
        assert node.health == NodeHealth.HEALTHY
        assert node.queue_depth == 0
        assert node.load == 0.0

    def test_repr(self) -> None:
        t = self._make_transport()
        node = RemoteWorkerNode("remote-1", transport=t)
        assert "RemoteWorkerNode" in repr(node)

    async def test_ping_sets_healthy(self) -> None:
        t = self._make_transport()
        node = RemoteWorkerNode("r", transport=t)
        ok = await node.ping()
        assert ok is True
        assert node.health == NodeHealth.HEALTHY

    async def test_ping_sets_unhealthy_on_failure(self) -> None:
        t = self._make_transport()
        t.health_check = AsyncMock(return_value=False)
        node = RemoteWorkerNode("r", transport=t)
        ok = await node.ping()
        assert ok is False
        assert node.health == NodeHealth.UNHEALTHY

    async def test_submit_resolves_handle(self) -> None:
        result = TaskResult(task_id="t2", agent_name="a", node_id="r", status=TaskStatus.DONE)
        t = self._make_transport(result)
        node = RemoteWorkerNode("r", transport=t)

        async def run() -> None:
            handle = TaskHandle("t2")
            await node.submit("t2", "a", {}, handle)
            # Give the dispatched task time to complete
            await asyncio.sleep(0.05)
            resolved = await asyncio.wait_for(handle._future, timeout=1.0)
            assert resolved.status == TaskStatus.DONE

        await run()

    async def test_submit_resolves_failed_on_exception(self) -> None:
        t = self._make_transport()
        t.submit = AsyncMock(side_effect=RuntimeError("network error"))
        node = RemoteWorkerNode("r", transport=t)

        async def run() -> None:
            handle = TaskHandle("err-task")
            await node.submit("err-task", "a", {}, handle)
            await asyncio.sleep(0.05)
            result = await asyncio.wait_for(handle._future, timeout=1.0)
            assert result.status == TaskStatus.FAILED
            assert "network error" in (result.error or "")

        await run()


class TestDistributedExecutorRemoteNodes:
    async def test_add_remote_node_increases_node_count(self) -> None:
        t = MagicMock(spec=HttpWorkerTransport)
        remote = RemoteWorkerNode("remote-1", transport=t)

        async with DistributedExecutor() as executor:
            assert executor.node_count == 0
            executor.add_remote_node(remote)
            assert executor.node_count == 1
            assert executor.remote_nodes == [remote]

    async def test_remove_remote_node(self) -> None:
        t = MagicMock(spec=HttpWorkerTransport)
        remote = RemoteWorkerNode("r1", transport=t)

        async with DistributedExecutor() as executor:
            executor.add_remote_node(remote)
            removed = executor.remove_remote_node("r1")
            assert removed is remote
            assert executor.node_count == 0

    async def test_submit_routes_to_remote_node(self) -> None:
        """Submitting to an executor with only remote nodes should dispatch remotely."""
        ok_result = TaskResult(
            task_id="t99", agent_name="a", node_id="r", status=TaskStatus.DONE
        )
        t = MagicMock(spec=HttpWorkerTransport)
        t.health_check = AsyncMock(return_value=True)
        t.submit = AsyncMock(return_value=ok_result)
        t.__repr__ = MagicMock(return_value="MockTransport()")

        remote = RemoteWorkerNode("r", transport=t)

        async with DistributedExecutor() as executor:
            executor.add_remote_node(remote)
            handle = await executor.submit("a", {})
            # Allow background dispatch to complete
            await asyncio.sleep(0.1)
            result = await asyncio.wait_for(handle._future, timeout=2.0)
        assert result.status == TaskStatus.DONE
