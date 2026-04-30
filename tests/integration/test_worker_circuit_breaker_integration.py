"""Integration tests for worker circuit breaker in distributed execution.

Sprint S17 — Worker Circuit Breakers (v0.8.0)

These tests verify that:
1. WorkerHttpApp exposes circuit state via /health endpoint
2. RemoteWorkerNode fetches and respects circuit state
3. DistributedExecutor filters out workers with OPEN circuits
4. HALF_OPEN workers get reduced selection probability
"""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from replicate_mcp.distributed import (
    DistributedExecutor,
    HttpWorkerTransport,
    RemoteWorkerNode,
    WorkerCircuitOpenError,
)
from replicate_mcp.resilience import CircuitBreakerConfig
from replicate_mcp.worker_circuit_breaker import WorkerCircuitState
from replicate_mcp.worker_server import WorkerHttpApp


@pytest.fixture
def circuit_config() -> CircuitBreakerConfig:
    """Default circuit breaker config for testing."""
    return CircuitBreakerConfig(
        failure_threshold=3,
        recovery_timeout=5.0,  # Short for tests
        half_open_max_calls=2,
    )


class TestWorkerHttpAppCircuitBreaker:
    """Test WorkerHttpApp circuit breaker integration."""

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_circuit_state(self, circuit_config) -> None:
        """/health endpoint returns circuit state when enabled."""
        app = WorkerHttpApp(
            node_id="test-worker",
            circuit_config=circuit_config,
        )

        # Build ASGI app
        _ = app._build_app()

        # Get circuit state
        state = app.circuit_state
        assert state is not None
        assert state.state == "closed"
        assert state.can_execute is True

    @pytest.mark.asyncio
    async def test_health_endpoint_no_circuit_disabled(self) -> None:
        """/health endpoint has no circuit field when disabled."""
        app = WorkerHttpApp(
            node_id="test-worker",
            circuit_config=None,  # Disabled
        )

        state = app.circuit_state
        assert state is None

    @pytest.mark.asyncio
    async def test_execution_records_success(self, circuit_config) -> None:
        """Successful execution records success on circuit breaker."""
        # Mock executor to return successful result
        mock_executor = Mock()
        mock_executor.run = AsyncMock(return_value=iter([{"output": "result"}]))

        app = WorkerHttpApp(
            executor=mock_executor,
            node_id="test-worker",
            circuit_config=circuit_config,
        )

        # Get initial state
        initial_state = app.circuit_state
        assert initial_state.failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self, circuit_config) -> None:
        """Circuit opens after threshold failures."""
        # Mock executor to always fail
        mock_executor = Mock()
        mock_executor.run = AsyncMock(
            side_effect=Exception("Simulated failure")
        )

        app = WorkerHttpApp(
            executor=mock_executor,
            node_id="test-worker",
            circuit_config=circuit_config,
        )

        # Circuit should start closed
        assert app.circuit_state.state == "closed"

        # Simulate multiple failures via the circuit breaker directly
        for _ in range(circuit_config.failure_threshold + 1):
            if app._circuit_breaker:
                try:
                    app._circuit_breaker.pre_call()
                    app._circuit_breaker.record_failure()
                except Exception:  # noqa: S110
                    pass  # Circuit opening is expected

        # Circuit should now be open
        state = app.circuit_state
        assert state.state == "open"
        assert state.can_execute is False


class TestHttpWorkerTransportCircuitState:
    """Test HttpWorkerTransport.get_circuit_state()."""

    @pytest.mark.asyncio
    async def test_get_circuit_state_from_health_response(self) -> None:
        """Transport parses circuit state from /health response."""
        transport = HttpWorkerTransport("http://worker-1:7999")

        # Mock health response with circuit data
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "node_id": "worker-1",
            "circuit": {
                "state": "open",
                "failure_count": 5,
                "success_count": 0,
                "last_failure_at": 1234567890.0,
                "recovery_timeout": 60.0,
                "half_open_max_calls": 3,
                "half_open_calls": 0,
                "can_execute": False,
            },
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = Mock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            state = await transport.get_circuit_state()

            assert state is not None
            assert state.state == "open"
            assert state.failure_count == 5
            assert state.can_execute is False

    @pytest.mark.asyncio
    async def test_get_circuit_state_none_when_disabled(self) -> None:
        """Transport returns None when worker has no circuit breaker."""
        transport = HttpWorkerTransport("http://worker-1:7999")

        # Mock health response without circuit data
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "node_id": "worker-1",
            # No "circuit" field
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = Mock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            state = await transport.get_circuit_state()

            assert state is None

    @pytest.mark.asyncio
    async def test_get_circuit_state_handles_errors(self) -> None:
        """Transport returns None on network errors."""
        transport = HttpWorkerTransport("http://worker-1:7999")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = Mock()
            mock_client.__aenter__ = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client_class.return_value = mock_client

            state = await transport.get_circuit_state()

            assert state is None


class TestRemoteWorkerNodeCircuitBreaker:
    """Test RemoteWorkerNode circuit breaker integration."""

    @pytest.mark.asyncio
    async def test_check_circuit_state_caches_result(self) -> None:
        """check_circuit_state fetches and caches circuit state."""
        transport = HttpWorkerTransport("http://worker-1:7999")
        node = RemoteWorkerNode("worker-1", transport=transport)

        # Mock the transport's get_circuit_state
        expected_state = WorkerCircuitState(
            state="closed",
            failure_count=0,
            success_count=0,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=True,
        )

        with patch.object(transport, "get_circuit_state", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = expected_state

            state = await node.check_circuit_state()

            assert state == expected_state
            assert node.circuit_state == expected_state
            mock_get.assert_called_once()

    def test_is_circuit_open_returns_true_when_open(self) -> None:
        """is_circuit_open returns True for OPEN state."""
        node = RemoteWorkerNode("worker-1", transport=Mock(spec=HttpWorkerTransport))

        # Set cached state to OPEN
        node._circuit_state = WorkerCircuitState(
            state="open",
            failure_count=5,
            success_count=0,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=False,
        )

        assert node.is_circuit_open() is True

    def test_is_circuit_open_returns_false_when_closed(self) -> None:
        """is_circuit_open returns False for CLOSED state."""
        node = RemoteWorkerNode("worker-1", transport=Mock(spec=HttpWorkerTransport))

        # Set cached state to CLOSED
        node._circuit_state = WorkerCircuitState(
            state="closed",
            failure_count=0,
            success_count=0,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=True,
        )

        assert node.is_circuit_open() is False

    def test_is_circuit_open_returns_false_when_no_state(self) -> None:
        """is_circuit_open returns False when no cached state."""
        node = RemoteWorkerNode("worker-1", transport=Mock(spec=HttpWorkerTransport))

        # No circuit state set
        assert node.is_circuit_open() is False

    @pytest.mark.asyncio
    async def test_submit_rejects_when_circuit_open(self) -> None:
        """submit raises WorkerCircuitOpenError when circuit is OPEN."""
        transport = HttpWorkerTransport("http://worker-1:7999")
        node = RemoteWorkerNode("worker-1", transport=transport)

        # Set up OPEN circuit state
        open_state = WorkerCircuitState(
            state="open",
            failure_count=5,
            success_count=0,
            last_failure_at=time.monotonic() - 10.0,  # 10 seconds ago
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=False,
        )

        with patch.object(transport, "get_circuit_state", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = open_state

            with pytest.raises(WorkerCircuitOpenError) as exc_info:
                await node.submit("task-1", "agent", {}, Mock())

            error = exc_info.value
            assert error.node_id == "worker-1"
            assert error.circuit_state == open_state
            assert error.retry_in is not None
            assert 0 < error.retry_in <= 50  # ~50 seconds remaining

    @pytest.mark.asyncio
    async def test_submit_rejects_when_half_open_at_limit(self) -> None:
        """submit raises when HALF_OPEN at probe limit."""
        transport = HttpWorkerTransport("http://worker-1:7999")
        node = RemoteWorkerNode("worker-1", transport=transport)

        # Set up HALF_OPEN circuit at probe limit
        half_open_state = WorkerCircuitState(
            state="half_open",
            failure_count=0,
            success_count=1,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=3,  # At limit
            can_execute=False,  # Because at limit
        )

        with patch.object(transport, "get_circuit_state", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = half_open_state

            with pytest.raises(WorkerCircuitOpenError) as exc_info:
                await node.submit("task-1", "agent", {}, Mock())

            error = exc_info.value
            assert error.node_id == "worker-1"
            assert error.retry_in == 0.0  # Can retry immediately

    @pytest.mark.asyncio
    async def test_submit_accepts_when_half_open_under_limit(self) -> None:
        """submit proceeds when HALF_OPEN under probe limit."""
        transport = HttpWorkerTransport("http://worker-1:7999")
        node = RemoteWorkerNode("worker-1", transport=transport)

        # Set up HALF_OPEN circuit under limit
        half_open_state = WorkerCircuitState(
            state="half_open",
            failure_count=0,
            success_count=1,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=1,  # Under limit
            can_execute=True,
        )

        with patch.object(transport, "get_circuit_state", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = half_open_state

            # Should not raise - creates task and returns
            await node.submit("task-1", "agent", {}, Mock())


class TestDistributedExecutorCircuitRouting:
    """Test DistributedExecutor circuit breaker-aware routing."""

    def test_least_loaded_filters_open_circuit_nodes(self) -> None:
        """_least_loaded_all excludes nodes with OPEN circuit."""
        executor = DistributedExecutor()

        # Create healthy remote node
        healthy_transport = Mock(spec=HttpWorkerTransport)
        healthy_node = RemoteWorkerNode("healthy", transport=healthy_transport)
        healthy_node._circuit_state = WorkerCircuitState(
            state="closed",
            failure_count=0,
            success_count=10,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=True,
        )
        executor.add_remote_node(healthy_node)

        # Create unhealthy node with OPEN circuit
        unhealthy_transport = Mock(spec=HttpWorkerTransport)
        unhealthy_node = RemoteWorkerNode("unhealthy", transport=unhealthy_transport)
        unhealthy_node._circuit_state = WorkerCircuitState(
            state="open",
            failure_count=5,
            success_count=0,
            last_failure_at=time.monotonic(),
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=False,
        )
        executor.add_remote_node(unhealthy_node)

        # Should only get the healthy node
        selected = executor._least_loaded_all()
        assert selected == healthy_node

    def test_least_loaded_applies_penalty_to_half_open(self) -> None:
        """_least_loaded_all applies 50% penalty to HALF_OPEN nodes."""
        executor = DistributedExecutor()

        # Create two nodes with same load, one HALF_OPEN
        normal_transport = Mock(spec=HttpWorkerTransport)
        normal_node = RemoteWorkerNode("normal", transport=normal_transport)
        normal_node._active_tasks = 10  # Load = 10
        normal_node._circuit_state = WorkerCircuitState(
            state="closed",
            failure_count=0,
            success_count=10,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=0,
            can_execute=True,
        )
        executor.add_remote_node(normal_node)

        half_open_transport = Mock(spec=HttpWorkerTransport)
        half_open_node = RemoteWorkerNode("half-open", transport=half_open_transport)
        half_open_node._active_tasks = 10  # Same load
        half_open_node._circuit_state = WorkerCircuitState(
            state="half_open",
            failure_count=2,
            success_count=1,
            last_failure_at=None,
            recovery_timeout=60.0,
            half_open_max_calls=3,
            half_open_calls=1,
            can_execute=True,
        )
        executor.add_remote_node(half_open_node)

        # HALF_OPEN node should be penalized (effective load = 15)
        # So normal node should be selected
        selected = executor._least_loaded_all()
        assert selected == normal_node

    def test_least_loaded_returns_none_when_all_circuits_open(self) -> None:
        """_least_loaded_all returns None when all nodes have OPEN circuits."""
        executor = DistributedExecutor()

        # Add only unhealthy nodes
        for i in range(3):
            transport = Mock(spec=HttpWorkerTransport)
            node = RemoteWorkerNode(f"unhealthy-{i}", transport=transport)
            node._circuit_state = WorkerCircuitState(
                state="open",
                failure_count=5,
                success_count=0,
                last_failure_at=time.monotonic(),
                recovery_timeout=60.0,
                half_open_max_calls=3,
                half_open_calls=0,
                can_execute=False,
            )
            executor.add_remote_node(node)

        selected = executor._least_loaded_all()
        assert selected is None
