"""Replicate MCP Agents — model-context-protocol orchestration for Replicate.

Phase 3 public surface:
    - :mod:`~replicate_mcp.discovery` — dynamic model discovery.
    - :mod:`~replicate_mcp.sdk` — fluent ``@agent`` decorator + builders.
    - :mod:`~replicate_mcp.qos` — QoS tiers + UCB1 / Thompson routing.
    - :mod:`~replicate_mcp.plugins` — pip-installable plugin ecosystem.
    - :mod:`~replicate_mcp.distributed` — 2-node distributed execution.
"""

from replicate_mcp.discovery import DiscoveryConfig, ModelDiscovery, discover_and_register
from replicate_mcp.distributed import DistributedExecutor, WorkerNode
from replicate_mcp.plugins import BasePlugin, PluginRegistry, load_plugins
from replicate_mcp.qos import AdaptiveRouter, QoSLevel, QoSPolicy, UCB1Router
from replicate_mcp.sdk import AgentBuilder, AgentContext, WorkflowBuilder, agent

__version__ = "0.4.0"

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
    # qos
    "QoSLevel",
    "QoSPolicy",
    "UCB1Router",
    "AdaptiveRouter",
    # plugins
    "BasePlugin",
    "PluginRegistry",
    "load_plugins",
    # distributed
    "WorkerNode",
    "DistributedExecutor",
]
