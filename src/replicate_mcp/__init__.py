"""Replicate MCP Agents — model-context-protocol orchestration for Replicate.

Phase 3 public surface:
    - :mod:`~replicate_mcp.discovery` — dynamic model discovery.
    - :mod:`~replicate_mcp.sdk` — fluent ``@agent`` decorator + builders.
    - :mod:`~replicate_mcp.qos` — QoS tiers + UCB1 / Thompson routing.
    - :mod:`~replicate_mcp.plugins` — pip-installable plugin ecosystem.
    - :mod:`~replicate_mcp.distributed` — 2-node distributed execution.

Phase 4 public surface:
    - :mod:`~replicate_mcp.distributed` — ``WorkerTransport``, ``HttpWorkerTransport``,
      ``RemoteWorkerNode`` for real network-distributed execution.
    - :mod:`~replicate_mcp.worker_server` — ``WorkerHttpApp``, ``serve_worker`` for
      running HTTP worker nodes.
    - :mod:`~replicate_mcp.server` — ``serve_http``, ``serve_streamable_http``,
      ``get_asgi_app`` for cloud-hosted MCP deployments.
    - :mod:`~replicate_mcp.sdk` — ``register_workflow``, ``get_workflow``, ``list_workflows``.
"""

from replicate_mcp.discovery import DiscoveryConfig, ModelDiscovery, discover_and_register
from replicate_mcp.distributed import (
    DistributedExecutor,
    HttpWorkerTransport,
    RemoteWorkerNode,
    WorkerNode,
    WorkerTransport,
)
from replicate_mcp.plugins import BasePlugin, PluginRegistry, load_plugins
from replicate_mcp.qos import AdaptiveRouter, QoSLevel, QoSPolicy, UCB1Router
from replicate_mcp.sdk import (
    AgentBuilder,
    AgentContext,
    WorkflowBuilder,
    agent,
    get_workflow,
    list_workflows,
    register_workflow,
)
from replicate_mcp.worker_server import WorkerHttpApp, serve_worker

__version__ = "0.5.0"

__all__ = [
    "__version__",
    # discovery
    "DiscoveryConfig",
    "ModelDiscovery",
    "discover_and_register",
    # sdk
    "agent",
    "AgentBuilder",
    "AgentContext",
    "WorkflowBuilder",
    "register_workflow",
    "get_workflow",
    "list_workflows",
    # qos
    "QoSLevel",
    "QoSPolicy",
    "UCB1Router",
    "AdaptiveRouter",
    # plugins
    "BasePlugin",
    "PluginRegistry",
    "load_plugins",
    # distributed — local
    "WorkerNode",
    "DistributedExecutor",
    # distributed — remote / transport
    "WorkerTransport",
    "HttpWorkerTransport",
    "RemoteWorkerNode",
    # worker server
    "WorkerHttpApp",
    "serve_worker",
]
