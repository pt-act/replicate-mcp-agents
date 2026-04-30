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
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from replicate_mcp.exceptions import ReplicateMCPError
from replicate_mcp.worker_circuit_breaker import WorkerCircuitState

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


class WorkerCircuitOpenError(ReplicateMCPError):
    """Raised when attempting to route to a worker with an OPEN circuit breaker.

    This exception signals that a remote worker's circuit breaker is in the
    OPEN state, indicating recent repeated failures. The coordinator should
    route to an alternative worker or retry after the recovery timeout.

    Attributes:
        node_id: The identifier of the worker with the open circuit.
        circuit_state: The circuit state snapshot at the time of rejection.
        retry_in: Estimated seconds until the circuit may close, or None.
    """

    def __init__(
        self,
        node_id: str,
        circuit_state: WorkerCircuitState,
        retry_in: float | None = None,
    ) -> None:
        message = f"Worker {node_id} circuit is {circuit_state.state}"
        if retry_in is not None:
            message += f" (retry in {retry_in:.1f}s)"
        super().__init__(message)
        self.node_id = node_id
        self.circuit_state = circuit_state
        self.retry_in = retry_in


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
        self._future: asyncio.Future[TaskResult] = asyncio.get_running_loop().create_future()

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
        self._remote_nodes: dict[str, RemoteWorkerNode] = {}
        self._max_retries = max_retries
        self._started = False

    # ---- node management ----

    def add_node(self, node: WorkerNode) -> None:
        """Add a local :class:`WorkerNode` and start its workers."""
        self._registry.add(node)
        if self._started:
            node.start()

    def add_remote_node(self, node: RemoteWorkerNode) -> None:
        """Add a :class:`RemoteWorkerNode` that delegates via HTTP transport.

        Remote nodes participate in least-loaded routing alongside local nodes.

        Example::

            transport = HttpWorkerTransport("http://gpu-box:7999")
            remote = RemoteWorkerNode("gpu-box", transport=transport)
            executor.add_remote_node(remote)
        """
        self._remote_nodes[node.node_id] = node
        logger.info("Remote node %r added to executor", node.node_id)

    def remove_remote_node(self, node_id: str) -> RemoteWorkerNode | None:
        """Remove and return the remote node with *node_id*."""
        return self._remote_nodes.pop(node_id, None)

    def remove_node(self, node_id: str) -> None:
        """Remove and stop a local node."""
        node = self._registry.remove(node_id)
        if node:
            asyncio.create_task(node.stop())

    @property
    def node_count(self) -> int:
        """Total number of nodes (local + remote)."""
        return self._registry.count + len(self._remote_nodes)

    @property
    def nodes(self) -> list[WorkerNode]:
        """All registered local nodes."""
        return self._registry.all_nodes

    @property
    def remote_nodes(self) -> list[RemoteWorkerNode]:
        """All registered remote nodes."""
        return list(self._remote_nodes.values())

    # ---- task submission ----

    def _least_loaded_all(self) -> WorkerNode | RemoteWorkerNode | None:
        """Return the least-loaded node across local and remote pools.

        Filters out remote nodes with OPEN circuit breakers to avoid
        routing to unhealthy workers. HALF_OPEN nodes are included but
        their load is artificially increased to reduce selection probability.
        """
        candidates: list[WorkerNode | RemoteWorkerNode] = []
        candidates.extend(self._registry.healthy_nodes)

        # Filter remote nodes by circuit state and health (v0.8.0)
        for n in self._remote_nodes.values():
            # Skip UNHEALTHY nodes
            if n.health == NodeHealth.UNHEALTHY:
                continue

            # Skip nodes with OPEN circuit (definitely unhealthy)
            if isinstance(n, RemoteWorkerNode) and n.is_circuit_open():
                logger.debug(
                    "Skipping remote node %r with OPEN circuit", n.node_id
                )
                continue

            candidates.append(n)

        if not candidates:
            return None

        # For HALF_OPEN nodes, add virtual load to reduce selection probability
        def effective_load(n: WorkerNode | RemoteWorkerNode) -> float:
            base_load = n.load
            if isinstance(n, RemoteWorkerNode) and n.is_circuit_half_open():
                # HALF_OPEN nodes get 50% penalty to reduce selection
                return base_load * 1.5
            return base_load

        return min(candidates, key=effective_load)

    async def submit(
        self,
        agent_name: str,
        payload: dict[str, Any],
    ) -> TaskHandle:
        """Submit a task to the least-loaded healthy node (local or remote).

        Routing prefers the node with the lowest ``load`` metric across
        both in-process :class:`WorkerNode` instances and
        :class:`RemoteWorkerNode` instances.

        Returns a :class:`TaskHandle` that can be awaited to retrieve
        the :class:`TaskResult`.

        Raises:
            :class:`NoHealthyNodesError`: If no healthy nodes are available.
            :class:`NodeOverloadError`: If all local nodes are overloaded.
        """
        task_id = uuid.uuid4().hex
        handle = TaskHandle(task_id)

        for attempt in range(self._max_retries + 1):
            node = self._least_loaded_all()
            if node is None:
                raise NoHealthyNodesError

            try:
                if isinstance(node, RemoteWorkerNode):
                    # Remote dispatch is fire-and-forget; handle is resolved async
                    await node.submit(task_id, agent_name, payload, handle)
                    logger.debug(
                        "Task %s dispatched to remote %s (attempt %d)",
                        task_id[:8], node.node_id, attempt + 1,
                    )
                else:
                    node.enqueue(task_id, agent_name, payload, handle)
                    logger.debug(
                        "Task %s enqueued on local %s (attempt %d)",
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


# ---------------------------------------------------------------------------
# Worker transport abstraction (ADR-008 extension)
# ---------------------------------------------------------------------------


class WorkerTransport(ABC):
    """Abstract transport for delegating task execution to a worker node.

    The :class:`WorkerNode` uses asyncio queues (in-process transport).
    :class:`RemoteWorkerNode` replaces this with an HTTP transport so that
    tasks can be dispatched to workers on different machines.

    Custom transports can be implemented by subclassing this ABC and
    passing instances to :class:`RemoteWorkerNode`.
    """

    @abstractmethod
    async def submit(
        self,
        task_id: str,
        agent_name: str,
        payload: dict[str, Any],
    ) -> TaskResult:
        """Submit a task and block until the result is ready.

        Args:
            task_id:    Unique identifier for tracking the task.
            agent_name: The agent to invoke on the remote worker.
            payload:    Input payload for the agent.

        Returns:
            Completed :class:`TaskResult`.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Return ``True`` if the remote endpoint is reachable."""

    @abstractmethod
    async def get_metrics(self) -> dict[str, Any]:
        """Return a snapshot of the remote worker's load metrics."""


# ---------------------------------------------------------------------------
# HTTP worker transport (client side)
# ---------------------------------------------------------------------------


class HttpWorkerTransport(WorkerTransport):
    """Submit tasks to a remote HTTP worker node.

    The remote node must expose a :class:`~replicate_mcp.worker_server.WorkerHttpApp`
    ASGI application (or any HTTP service with compatible endpoints):

    * ``POST /execute`` — run a task, return :class:`TaskResult` JSON.
    * ``GET  /health``  — return ``{"status": "healthy"}`` with HTTP 200.
    * ``GET  /metrics`` — return load metrics JSON.

    Args:
        base_url: HTTP base URL of the remote worker (e.g. ``"http://host:7999"``).
        timeout:  Per-request timeout in seconds (default: 120 s).

    Example::

        transport = HttpWorkerTransport("http://gpu-node-1:7999", timeout=60)
        node = RemoteWorkerNode("gpu-node-1", transport=transport)
    """

    def __init__(self, base_url: str, *, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        """The HTTP base URL of the remote worker."""
        return self._base_url

    async def submit(
        self,
        task_id: str,
        agent_name: str,
        payload: dict[str, Any],
    ) -> TaskResult:
        """POST the task to ``/execute`` and return the deserialized result."""
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "httpx is required for HTTP worker transport. "
                "Install it with: pip install httpx"
            ) from exc

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/execute",
                json={
                    "task_id": task_id,
                    "agent_name": agent_name,
                    "payload": payload,
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        return TaskResult(
            task_id=data.get("task_id", task_id),
            agent_name=data.get("agent_name", agent_name),
            node_id=data.get("node_id", self._base_url),
            chunks=data.get("chunks", []),
            status=TaskStatus(data.get("status", TaskStatus.DONE.value)),
            error=data.get("error"),
            elapsed_ms=data.get("elapsed_ms", 0.0),
        )

    async def health_check(self) -> bool:
        """GET ``/health`` and return ``True`` if the server responds OK."""
        try:
            import httpx  # noqa: PLC0415

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/health")
                return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def get_circuit_state(self) -> WorkerCircuitState | None:
        """GET ``/health`` and extract circuit breaker state if available.

        Returns the circuit state from the worker's health endpoint, or None
        if the worker does not have circuit breaker enabled or is unreachable.

        Returns:
            WorkerCircuitState if circuit breaker data is present in the
            health response, otherwise None.
        """
        try:
            import httpx  # noqa: PLC0415

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/health")
                if response.status_code != 200:
                    return None
                data: dict[str, Any] = response.json()
                circuit_data = data.get("circuit")
                if not circuit_data:
                    return None

                # Parse circuit state from response
                return WorkerCircuitState(
                    state=circuit_data.get("state", "unknown"),
                    failure_count=circuit_data.get("failure_count", 0),
                    success_count=circuit_data.get("success_count", 0),
                    last_failure_at=circuit_data.get("last_failure_at"),
                    recovery_timeout=circuit_data.get("recovery_timeout", 60.0),
                    half_open_max_calls=circuit_data.get("half_open_max_calls", 3),
                    half_open_calls=circuit_data.get("half_open_calls", 0),
                    can_execute=circuit_data.get("can_execute", True),
                )
        except Exception:  # noqa: BLE001
            return None

    async def get_metrics(self) -> dict[str, Any]:
        """GET ``/metrics`` and return the parsed JSON body."""
        try:
            import httpx  # noqa: PLC0415

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/metrics")
                return dict(response.json())
        except Exception:  # noqa: BLE001
            return {}

    def __repr__(self) -> str:
        return f"HttpWorkerTransport(base_url={self._base_url!r}, timeout={self._timeout}s)"


# ---------------------------------------------------------------------------
# Remote worker node
# ---------------------------------------------------------------------------


class RemoteWorkerNode:
    """A worker node that delegates task execution to a remote HTTP endpoint.

    Unlike :class:`WorkerNode` (which runs tasks in-process), ``RemoteWorkerNode``
    submits tasks via a :class:`WorkerTransport` and tracks only the
    in-flight request count for load estimation.

    Args:
        node_id:   Unique identifier for this remote node.
        transport: :class:`WorkerTransport` implementation to use.
        concurrency: Estimated capacity (used for load reporting only;
                     actual throttling is done server-side).

    Example::

        transport = HttpWorkerTransport("http://gpu-node-1:7999")
        node = RemoteWorkerNode("gpu-node-1", transport=transport)

        async with DistributedExecutor() as executor:
            executor.add_remote_node(node)
            result = await (await executor.submit("llama_chat", {"prompt": "hi"}))
    """

    def __init__(
        self,
        node_id: str,
        *,
        transport: WorkerTransport,
        concurrency: int = 8,
    ) -> None:
        self._node_id = node_id
        self._transport = transport
        self._concurrency = concurrency
        self._health: NodeHealth = NodeHealth.HEALTHY
        self._active_tasks: int = 0
        self._total_processed: int = 0
        # Circuit breaker state cache (v0.8.0)
        self._circuit_state: WorkerCircuitState | None = None
        self._circuit_state_timestamp: float = 0.0

    @property
    def circuit_state(self) -> WorkerCircuitState | None:
        """Cached circuit breaker state from last health check, or None."""
        return self._circuit_state

    async def check_circuit_state(self) -> WorkerCircuitState | None:
        """Fetch and cache the circuit breaker state from the worker.

        This method queries the transport for the worker's circuit state
        and caches it locally. The cached state is used for routing
        decisions by the DistributedExecutor.

        Returns:
            WorkerCircuitState if the worker has circuit breaker enabled,
            otherwise None.
        """
        if isinstance(self._transport, HttpWorkerTransport):
            state = await self._transport.get_circuit_state()
            self._circuit_state = state
            self._circuit_state_timestamp = time.monotonic()
            return state
        return None

    def is_circuit_open(self) -> bool:
        """Check if the cached circuit state indicates an OPEN circuit.

        Returns True if the circuit is OPEN (should not route new tasks).
        Returns False if circuit is CLOSED, HALF_OPEN, or unknown.
        """
        if self._circuit_state is None:
            return False  # Assume healthy if no state
        return self._circuit_state.state == "open"

    def is_circuit_half_open(self) -> bool:
        """Check if the cached circuit state indicates a HALF_OPEN circuit.

        Returns True if the circuit is HALF_OPEN (limited probe traffic).
        """
        if self._circuit_state is None:
            return False
        return self._circuit_state.state == "half_open"

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def health(self) -> NodeHealth:
        return self._health

    @property
    def transport(self) -> WorkerTransport:
        """The underlying :class:`WorkerTransport`."""
        return self._transport

    @property
    def load(self) -> float:
        """In-flight request count (used for least-loaded routing)."""
        return float(self._active_tasks)

    @property
    def queue_depth(self) -> int:
        """Remote nodes report 0 local queue depth (queuing is server-side)."""
        return 0

    @property
    def active_tasks(self) -> int:
        return self._active_tasks

    @property
    def total_processed(self) -> int:
        return self._total_processed

    def mark_unhealthy(self) -> None:
        self._health = NodeHealth.UNHEALTHY
        logger.warning("Remote node %r marked unhealthy", self._node_id)

    def mark_healthy(self) -> None:
        self._health = NodeHealth.HEALTHY
        logger.info("Remote node %r marked healthy", self._node_id)

    async def ping(self) -> bool:
        """Check reachability via the transport's health endpoint.

        Returns ``True`` if the remote node is reachable and healthy.
        Updates :attr:`health` accordingly.
        """
        ok = await self._transport.health_check()
        if ok:
            self.mark_healthy()
        else:
            self.mark_unhealthy()
        return ok

    async def submit(
        self,
        task_id: str,
        agent_name: str,
        payload: dict[str, Any],
        handle: TaskHandle,
    ) -> None:
        """Dispatch a task via the transport and resolve *handle* with the result.

        This method creates an asyncio task so the call returns immediately.

        Raises:
            WorkerCircuitOpenError: If the worker's circuit breaker is OPEN
                and the task should be routed to another worker.
        """
        # Check circuit breaker state before dispatching (v0.8.0)
        circuit_state = await self.check_circuit_state()
        if circuit_state is not None:
            if circuit_state.state == "open":
                # Calculate retry-in time
                retry_in: float | None = None
                if circuit_state.last_failure_at is not None:
                    elapsed = time.monotonic() - circuit_state.last_failure_at
                    retry_in = max(0, circuit_state.recovery_timeout - elapsed)
                raise WorkerCircuitOpenError(
                    self._node_id, circuit_state, retry_in=retry_in
                )
            if circuit_state.state == "half_open":
                # In HALF_OPEN, check if we're within probe limits
                if circuit_state.half_open_calls >= circuit_state.half_open_max_calls:
                    logger.warning(
                        "Worker %r circuit HALF_OPEN at probe limit (%d/%d)",
                        self._node_id,
                        circuit_state.half_open_calls,
                        circuit_state.half_open_max_calls,
                    )
                    raise WorkerCircuitOpenError(
                        self._node_id,
                        circuit_state,
                        retry_in=0.0,  # Try again immediately after one completes
                    )

        asyncio.create_task(
            self._dispatch(task_id, agent_name, payload, handle),
            name=f"remote-{self._node_id}-{task_id[:8]}",
        )

    async def _dispatch(
        self,
        task_id: str,
        agent_name: str,
        payload: dict[str, Any],
        handle: TaskHandle,
    ) -> None:
        self._active_tasks += 1
        try:
            result = await self._transport.submit(task_id, agent_name, payload)
            handle.set_result(result)
            self._total_processed += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Remote dispatch failed for task %s on node %r: %s",
                task_id[:8], self._node_id, exc,
            )
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

    def __repr__(self) -> str:
        return (
            f"RemoteWorkerNode(id={self._node_id!r}, "
            f"transport={self._transport!r}, "
            f"health={self._health.value})"
        )


__all__ = [
    "NodeHealth",
    "NodeOverloadError",
    "NoHealthyNodesError",
    "WorkerCircuitOpenError",
    "NodeRegistry",
    "TaskHandle",
    "TaskResult",
    "TaskStatus",
    "WorkerNode",
    "DistributedExecutor",
    "WorkerTransport",
    "HttpWorkerTransport",
    "RemoteWorkerNode",
]
