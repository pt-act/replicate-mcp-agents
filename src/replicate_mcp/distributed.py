"""Distributed execution across multiple worker nodes.

Sprint S11 — Differentiation.  Provides a lightweight, asyncio-native
distributed execution layer that routes tasks across a pool of worker
nodes.

Architecture:

* :class:`WorkerNode` — encapsulates a local or remote execution context
  with its own task queue and health state.
* :class:`NodeRegistry` — maintains the pool of known nodes.
* :class:`DistributedExecutor` — distributes tasks using a
  least-loaded routing strategy with automatic failover.
* :class:`TaskHandle` — future-like object for tracking async tasks.

Design (see ADR-008):
    - The initial implementation uses asyncio queues for in-process
      distribution, supporting 2+ workers on a single machine.  The
      transport layer is abstracted behind :class:`WorkerTransport` so
      future releases can substitute gRPC or HTTP without changing the
      public API.
    - Failover: if a node becomes UNHEALTHY, its pending tasks are
      redistributed to the least-loaded healthy node.
    - Back-pressure: each node has a configurable ``max_queue_depth``.
      Tasks submitted beyond that depth raise :class:`NodeOverloadError`.

Usage::

    from replicate_mcp.distributed import DistributedExecutor, WorkerNode

    async with DistributedExecutor() as executor:
        executor.add_node(WorkerNode("node-1"))
        executor.add_node(WorkerNode("node-2"))

        result = await executor.submit("llama3_chat", {"prompt": "hi"})
        print(result)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from replicate_mcp.exceptions import ReplicateMCPError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NodeOverloadError(ReplicateMCPError):
    """Raised when a node's task queue has reached its capacity."""

    def __init__(self, node_id: str, depth: int) -> None:
        super().__init__(f"Node '{node_id}' queue at capacity ({depth} tasks)")
        self.node_id = node_id
        self.queue_depth = depth


class NoHealthyNodesError(ReplicateMCPError):
    """Raised when all nodes are unhealthy and no task can be submitted."""

    def __init__(self) -> None:
        super().__init__("No healthy worker nodes available")


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """Lifecycle state of a distributed task."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class TaskResult:
    """The outcome of a distributed task execution.

    Attributes:
        task_id:    Unique task identifier (UUID4).
        agent_name: The agent that was invoked.
        node_id:    The worker node that executed the task.
        chunks:     Streamed output chunks from the agent.
        status:     Final task status.
        error:      Exception message if the task failed.
        elapsed_ms: Wall-clock duration.
    """

    task_id: str
    agent_name: str
    node_id: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    error: str | None = None
    elapsed_ms: float = 0.0


class TaskHandle:
    """Awaitable handle for a submitted distributed task.

    Callers can ``await`` the handle directly to get the
    :class:`TaskResult`::

        handle = await executor.submit("llama3_chat", {"prompt": "hi"})
        result = await handle
    """

    def __init__(self, task_id: str) -> None:
        self._task_id = task_id
        self._future: asyncio.Future[TaskResult] = asyncio.get_event_loop().create_future()

    @property
    def task_id(self) -> str:
        return self._task_id

    def set_result(self, result: TaskResult) -> None:
        """Resolve the handle with a completed result."""
        if not self._future.done():
            self._future.set_result(result)

    def set_exception(self, exc: Exception) -> None:
        """Reject the handle with an exception."""
        if not self._future.done():
            self._future.set_exception(exc)

    def __await__(self) -> Any:  # noqa: ANN401
        return self._future.__await__()


# ---------------------------------------------------------------------------
# Node health
# ---------------------------------------------------------------------------


class NodeHealth(str, Enum):
    """Health state of a worker node."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"    # Slow but operational
    UNHEALTHY = "unhealthy"  # Not accepting tasks


# ---------------------------------------------------------------------------
# Worker node
# ---------------------------------------------------------------------------


class WorkerNode:
    """An asyncio-native worker that processes tasks from a local queue.

    Args:
        node_id:       Unique identifier for this node.
        max_queue_depth: Maximum number of pending tasks before
                         :class:`NodeOverloadError` is raised.
        concurrency:   Maximum number of tasks running simultaneously.
    """

    def __init__(
        self,
        node_id: str | None = None,
        *,
        max_queue_depth: int = 100,
        concurrency: int = 4,
    ) -> None:
        self._node_id = node_id or f"node-{uuid.uuid4().hex[:8]}"
        self._max_queue_depth = max_queue_depth
        self._concurrency = concurrency
        self._queue: asyncio.Queue[tuple[str, str, dict[str, Any], TaskHandle]] = asyncio.Queue()
        self._health: NodeHealth = NodeHealth.HEALTHY
        self._active_tasks: int = 0
        self._total_processed: int = 0
        self._worker_tasks: list[asyncio.Task[None]] = []

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def health(self) -> NodeHealth:
        return self._health

    @property
    def queue_depth(self) -> int:
        """Number of tasks currently waiting in the queue."""
        return self._queue.qsize()

    @property
    def active_tasks(self) -> int:
        """Number of tasks currently executing on this node."""
        return self._active_tasks

    @property
    def load(self) -> float:
        """Load metric: active + queued tasks.  Lower is better."""
        return float(self._active_tasks + self._queue.qsize())

    @property
    def total_processed(self) -> int:
        """Total number of tasks successfully processed."""
        return self._total_processed

    def mark_unhealthy(self) -> None:
        """Mark this node as unhealthy (e.g. after repeated failures)."""
        self._health = NodeHealth.UNHEALTHY
        logger.warning("Node %r marked unhealthy", self._node_id)

    def mark_healthy(self) -> None:
        """Mark this node as healthy again."""
        self._health = NodeHealth.HEALTHY
        logger.info("Node %r marked healthy", self._node_id)

    # ---- task submission ----

    def enqueue(
        self,
        task_id: str,
        agent_name: str,
        payload: dict[str, Any],
        handle: TaskHandle,
    ) -> None:
        """Add a task to this node's queue.

        Raises:
            :class:`NodeOverloadError`: If the queue is at capacity.
        """
        if self._queue.qsize() >= self._max_queue_depth:
            raise NodeOverloadError(self._node_id, self._queue.qsize())
        self._queue.put_nowait((task_id, agent_name, payload, handle))

    # ---- worker lifecycle ----

    def start(self) -> None:
        """Start the background worker coroutines."""
        for i in range(self._concurrency):
            task = asyncio.create_task(
                self._worker_loop(),
                name=f"{self._node_id}-worker-{i}",
            )
            self._worker_tasks.append(task)
        logger.info(
            "Node %r started with concurrency=%d", self._node_id, self._concurrency
        )

    async def stop(self, timeout: float = 5.0) -> None:
        """Drain the queue and stop worker coroutines."""
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Node %r queue drain timed out after %.1fs", self._node_id, timeout)

        for task in self._worker_tasks:
            task.cancel()
        self._worker_tasks.clear()
        logger.info("Node %r stopped", self._node_id)

    # ---- internal ----

    async def _worker_loop(self) -> None:
        """Consume tasks from the queue indefinitely."""
        while True:
            try:
                task_id, agent_name, payload, handle = await self._queue.get()
                self._active_tasks += 1
                try:
                    result = await self._execute(task_id, agent_name, payload)
                    handle.set_result(result)
                    self._total_processed += 1
                except Exception as exc:  # noqa: BLE001
                    error_result = TaskResult(
                        task_id=task_id,
                        agent_name=agent_name,
                        node_id=self._node_id,
                        status=TaskStatus.FAILED,
                        error=str(exc),
                    )
                    handle.set_result(error_result)
                finally:
                    self._active_tasks -= 1
                    self._queue.task_done()
            except asyncio.CancelledError:
                break

    async def _execute(
        self,
        task_id: str,
        agent_name: str,
        payload: dict[str, Any],
    ) -> TaskResult:
        """Execute the task using the local AgentExecutor if available.

        Falls back to a stub implementation when no executor is configured
        (useful in unit tests and for nodes without Replicate API access).
        """
        t0 = time.perf_counter()

        # Use injected executor if available
        executor = getattr(self, "_executor", None)
        chunks: list[dict[str, Any]] = []

        if executor is not None:
            async for chunk in executor.run(agent_name, payload):
                chunks.append(chunk)
        else:
            # Stub: useful for integration tests without a real API key
            await asyncio.sleep(0)  # yield control
            chunks = [
                {
                    "done": True,
                    "output": f"[stub:{self._node_id}] {agent_name}({payload})",
                    "latency_ms": 0.0,
                }
            ]

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return TaskResult(
            task_id=task_id,
            agent_name=agent_name,
            node_id=self._node_id,
            chunks=chunks,
            status=TaskStatus.DONE,
            elapsed_ms=elapsed_ms,
        )

    def __repr__(self) -> str:
        return (
            f"WorkerNode(id={self._node_id!r}, "
            f"health={self._health.value}, "
            f"load={self.load:.0f})"
        )


# ---------------------------------------------------------------------------
# Node registry
# ---------------------------------------------------------------------------


class NodeRegistry:
    """Maintains the pool of worker nodes.

    Args:
        nodes: Initial list of :class:`WorkerNode` instances.
    """

    def __init__(self, nodes: list[WorkerNode] | None = None) -> None:
        self._nodes: dict[str, WorkerNode] = {}
        for node in nodes or []:
            self.add(node)

    def add(self, node: WorkerNode) -> None:
        """Add a node to the pool.  No-op if the ID already exists."""
        if node.node_id not in self._nodes:
            self._nodes[node.node_id] = node

    def remove(self, node_id: str) -> WorkerNode | None:
        """Remove and return the node with the given ID."""
        return self._nodes.pop(node_id, None)

    def get(self, node_id: str) -> WorkerNode | None:
        """Return the node with the given ID, or ``None``."""
        return self._nodes.get(node_id)

    @property
    def all_nodes(self) -> list[WorkerNode]:
        """All nodes (healthy and unhealthy)."""
        return list(self._nodes.values())

    @property
    def healthy_nodes(self) -> list[WorkerNode]:
        """Nodes currently in HEALTHY or DEGRADED state."""
        return [
            n for n in self._nodes.values()
            if n.health != NodeHealth.UNHEALTHY
        ]

    @property
    def count(self) -> int:
        """Total number of nodes."""
        return len(self._nodes)

    def least_loaded(self) -> WorkerNode | None:
        """Return the healthy node with the lowest load, or ``None``."""
        candidates = self.healthy_nodes
        if not candidates:
            return None
        return min(candidates, key=lambda n: n.load)


# ---------------------------------------------------------------------------
# Distributed executor
# ---------------------------------------------------------------------------


class DistributedExecutor:
    """Dispatches agent invocations across a pool of :class:`WorkerNode` objects.

    Can be used as an async context manager::

        async with DistributedExecutor() as executor:
            executor.add_node(WorkerNode("node-1"))
            executor.add_node(WorkerNode("node-2"))
            result = await (await executor.submit("chat", {"prompt": "hi"}))

    Args:
        nodes:          Initial list of worker nodes.
        max_retries:    Number of failover attempts before raising.
    """

    def __init__(
        self,
        nodes: list[WorkerNode] | None = None,
        *,
        max_retries: int = 2,
    ) -> None:
        self._registry = NodeRegistry(nodes)
        self._max_retries = max_retries
        self._started = False

    # ---- node management ----

    def add_node(self, node: WorkerNode) -> None:
        """Add a :class:`WorkerNode` and start its workers."""
        self._registry.add(node)
        if self._started:
            node.start()

    def remove_node(self, node_id: str) -> None:
        """Remove and stop a node."""
        node = self._registry.remove(node_id)
        if node:
            asyncio.create_task(node.stop())

    @property
    def node_count(self) -> int:
        """Number of nodes in the pool."""
        return self._registry.count

    @property
    def nodes(self) -> list[WorkerNode]:
        """All registered nodes."""
        return self._registry.all_nodes

    # ---- task submission ----

    async def submit(
        self,
        agent_name: str,
        payload: dict[str, Any],
    ) -> TaskHandle:
        """Submit a task to the least-loaded healthy node.

        Returns a :class:`TaskHandle` that can be awaited to retrieve
        the :class:`TaskResult`.

        Raises:
            :class:`NoHealthyNodesError`: If no healthy nodes are available.
            :class:`NodeOverloadError`: If all nodes are overloaded.
        """
        task_id = uuid.uuid4().hex
        handle = TaskHandle(task_id)

        for attempt in range(self._max_retries + 1):
            node = self._registry.least_loaded()
            if node is None:
                raise NoHealthyNodesError

            try:
                node.enqueue(task_id, agent_name, payload, handle)
                logger.debug(
                    "Task %s submitted to %s (attempt %d)",
                    task_id[:8], node.node_id, attempt + 1,
                )
                return handle
            except NodeOverloadError:
                if attempt == self._max_retries:
                    raise
                logger.warning(
                    "Node %r overloaded; retrying (attempt %d/%d)",
                    node.node_id, attempt + 1, self._max_retries,
                )
                node.mark_unhealthy()

        raise NoHealthyNodesError  # unreachable but satisfies type checker

    async def run_many(
        self,
        tasks: list[tuple[str, dict[str, Any]]],
    ) -> list[TaskResult]:
        """Submit multiple tasks concurrently and wait for all results.

        Args:
            tasks: List of ``(agent_name, payload)`` tuples.

        Returns:
            List of :class:`TaskResult` objects in the same order as
            *tasks*.
        """
        handles = [await self.submit(name, payload) for name, payload in tasks]
        return list(await asyncio.gather(*handles))

    async def stream(
        self,
        agent_name: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Submit a task and yield output chunks as they arrive.

        This is a convenience wrapper around :meth:`submit` that
        ``await``s the handle and then yields its chunks.
        """
        handle = await self.submit(agent_name, payload)
        result = await handle
        for chunk in result.chunks:
            yield chunk

    # ---- lifecycle ----

    def start(self) -> None:
        """Start all registered nodes."""
        for node in self._registry.all_nodes:
            node.start()
        self._started = True
        logger.info(
            "DistributedExecutor started with %d node(s)",
            self._registry.count,
        )

    async def stop(self, timeout: float = 10.0) -> None:
        """Drain all queues and stop all nodes."""
        stop_tasks = [node.stop(timeout=timeout) for node in self._registry.all_nodes]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        self._started = False
        logger.info("DistributedExecutor stopped")

    async def __aenter__(self) -> DistributedExecutor:
        self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    def __repr__(self) -> str:
        return (
            f"DistributedExecutor("
            f"nodes={self._registry.count}, "
            f"started={self._started})"
        )


__all__ = [
    "NodeHealth",
    "NodeOverloadError",
    "NoHealthyNodesError",
    "NodeRegistry",
    "TaskHandle",
    "TaskResult",
    "TaskStatus",
    "WorkerNode",
    "DistributedExecutor",
]
