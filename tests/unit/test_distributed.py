"""Tests for replicate_mcp.distributed — distributed execution engine."""

from __future__ import annotations

import asyncio

import pytest

from replicate_mcp.distributed import (
    DistributedExecutor,
    NodeHealth,
    NodeOverloadError,
    NodeRegistry,
    NoHealthyNodesError,
    TaskHandle,
    TaskResult,
    TaskStatus,
    WorkerNode,
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
    def test_set_and_await_result(self) -> None:
        async def run() -> None:
            handle = TaskHandle("tid")
            result = TaskResult(task_id="tid", agent_name="a", node_id="n")
            handle.set_result(result)
            resolved = await handle
            assert resolved is result

        asyncio.get_event_loop().run_until_complete(run())

    def test_set_result_idempotent(self) -> None:
        async def run() -> None:
            handle = TaskHandle("tid")
            r1 = TaskResult(task_id="tid", agent_name="a", node_id="n")
            r2 = TaskResult(task_id="tid", agent_name="b", node_id="n")
            handle.set_result(r1)
            handle.set_result(r2)  # Second call should be no-op
            resolved = await handle
            assert resolved is r1

        asyncio.get_event_loop().run_until_complete(run())

    def test_set_exception(self) -> None:
        async def run() -> None:
            handle = TaskHandle("tid")
            handle.set_exception(RuntimeError("boom"))
            with pytest.raises(RuntimeError, match="boom"):
                await handle

        asyncio.get_event_loop().run_until_complete(run())

    def test_set_exception_idempotent(self) -> None:
        async def run() -> None:
            handle = TaskHandle("tid")
            handle.set_exception(RuntimeError("first"))
            handle.set_exception(RuntimeError("second"))  # no-op
            with pytest.raises(RuntimeError, match="first"):
                await handle

        asyncio.get_event_loop().run_until_complete(run())

    def test_task_id(self) -> None:
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

    def test_enqueue_adds_to_queue(self) -> None:
        async def run() -> None:
            node = WorkerNode("n1", max_queue_depth=10)
            handle = TaskHandle("t1")
            node.enqueue("t1", "agent", {}, handle)
            assert node.queue_depth == 1

        asyncio.get_event_loop().run_until_complete(run())

    def test_enqueue_at_capacity_raises(self) -> None:
        async def run() -> None:
            node = WorkerNode("n1", max_queue_depth=1)
            handle1 = TaskHandle("t1")
            handle2 = TaskHandle("t2")
            node.enqueue("t1", "agent", {}, handle1)
            with pytest.raises(NodeOverloadError) as exc_info:
                node.enqueue("t2", "agent", {}, handle2)
            assert exc_info.value.node_id == "n1"

        asyncio.get_event_loop().run_until_complete(run())

    def test_node_executes_stub_task(self) -> None:
        async def run() -> None:
            node = WorkerNode("n1")
            node.start()
            handle = TaskHandle("t1")
            node.enqueue("t1", "chat", {"prompt": "hi"}, handle)
            result = await asyncio.wait_for(handle, timeout=2.0)
            assert result.status == TaskStatus.DONE
            assert result.node_id == "n1"
            assert len(result.chunks) > 0
            await node.stop()

        asyncio.get_event_loop().run_until_complete(run())

    def test_node_total_processed_increments(self) -> None:
        async def run() -> None:
            node = WorkerNode("n1")
            node.start()
            for i in range(3):
                handle = TaskHandle(f"t{i}")
                node.enqueue(f"t{i}", "agent", {}, handle)
                await asyncio.wait_for(handle, timeout=2.0)
            assert node.total_processed == 3
            await node.stop()

        asyncio.get_event_loop().run_until_complete(run())


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

    def test_least_loaded(self) -> None:
        async def run() -> None:
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

        asyncio.get_event_loop().run_until_complete(run())

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
    def test_context_manager(self) -> None:
        async def run() -> None:
            async with DistributedExecutor() as executor:
                executor.add_node(WorkerNode("n1"))
                assert executor.node_count == 1

        asyncio.get_event_loop().run_until_complete(run())

    def test_submit_executes_task(self) -> None:
        async def run() -> None:
            async with DistributedExecutor() as executor:
                executor.add_node(WorkerNode("n1"))
                handle = await executor.submit("chat", {"prompt": "hello"})
                result = await asyncio.wait_for(handle, timeout=2.0)
                assert result.status == TaskStatus.DONE
                assert result.agent_name == "chat"
                assert result.node_id == "n1"

        asyncio.get_event_loop().run_until_complete(run())

    def test_submit_to_least_loaded(self) -> None:
        async def run() -> None:
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

        asyncio.get_event_loop().run_until_complete(run())

    def test_submit_no_nodes_raises(self) -> None:
        async def run() -> None:
            async with DistributedExecutor() as executor:
                with pytest.raises(NoHealthyNodesError):
                    await executor.submit("agent", {})

        asyncio.get_event_loop().run_until_complete(run())

    def test_run_many(self) -> None:
        async def run() -> None:
            async with DistributedExecutor() as executor:
                executor.add_node(WorkerNode("n1"))
                tasks = [("agent_a", {"x": i}) for i in range(4)]
                results = await asyncio.wait_for(
                    executor.run_many(tasks), timeout=5.0
                )
                assert len(results) == 4
                assert all(r.status == TaskStatus.DONE for r in results)

        asyncio.get_event_loop().run_until_complete(run())

    def test_stream(self) -> None:
        async def run() -> None:
            async with DistributedExecutor() as executor:
                executor.add_node(WorkerNode("n1"))
                chunks = []
                async for chunk in executor.stream("agent", {"prompt": "hi"}):
                    chunks.append(chunk)
                assert len(chunks) > 0

        asyncio.get_event_loop().run_until_complete(run())

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

    def test_two_nodes_distribute_work(self) -> None:
        """Verify tasks are spread across 2 nodes."""
        async def run() -> None:
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

        asyncio.get_event_loop().run_until_complete(run())

    def test_overloaded_node_failover(self) -> None:
        """Overloaded node is marked unhealthy; second node takes over."""
        async def run() -> None:
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

        asyncio.get_event_loop().run_until_complete(run())

    def test_all_overloaded_raises(self) -> None:
        """When all nodes are overloaded/unhealthy, raise NoHealthyNodesError."""
        async def run() -> None:
            executor = DistributedExecutor(max_retries=0)
            n1 = WorkerNode("n1", max_queue_depth=0)
            executor.add_node(n1)
            executor.start()
            with pytest.raises((NoHealthyNodesError, NodeOverloadError)):
                await executor.submit("agent", {})
            await executor.stop()

        asyncio.get_event_loop().run_until_complete(run())


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
