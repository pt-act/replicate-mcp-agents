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

Phase 5a public surface:
    - :mod:`~replicate_mcp.utils.router_state` — ``RouterStateManager`` for durable
      routing intelligence across restarts.
    - :mod:`~replicate_mcp.utils.audit` — ``AuditLogger`` + ``AuditRecord`` for local
      invocation audit log and cost dashboard.
    - :mod:`~replicate_mcp.cache` — ``ResultCache`` for content-addressed result
      caching during development.
    - :mod:`~replicate_mcp.sdk` — ``load_workflows_file`` for YAML workflow config.
    - Plugin middleware: ``on_agent_run`` / ``on_agent_result`` hooks now return
      optional replacement payloads / chunk-lists for mutable middleware.
    - ``AgentExecutor`` now accepts ``plugin_registry``, ``audit_logger``, ``cache``.

Phase 6 public surface:
    - :mod:`~replicate_mcp.worker_circuit_breaker` — ``WorkerCircuitBreaker``,
      ``WorkerCircuitState`` for distributed worker reliability tracking (v0.8.0).
    - :mod:`~replicate_mcp.distributed` — ``WorkerCircuitOpenError`` for circuit
      breaker-aware routing (v0.8.0).

Phase 6 public surface:
    - :mod:`~replicate_mcp.exceptions` — ``RetryableError``, ``NonRetryableError``,
      ``AuthenticationError``, ``RateLimitError``, ``ServerError``, ``ClientError``
      for error classification in retry logic.
    - :func:`~replicate_mcp.resilience.is_retryable_error` — classifies errors
      as retryable or non-retryable before deciding to retry.
    - :class:`~replicate_mcp.routing.RoutingDecision` — explains *why* a model
      was selected; returned by ``CostAwareRouter.select_model_explain()``.
    - :mod:`~replicate_mcp.plugins.builtin` — built-in guardrail plugins:
      ``PIIMaskPlugin``, ``ContentFilterPlugin``, ``CostCapPlugin``.
    - ``replicate-agent doctor`` — diagnostic command for first-run health checks.
    - ``replicate-agent agents run --dry-run`` — cost estimation without API call.

Phase 7 public surface (optional extras):
    - :mod:`~replicate_mcp.latitude` — Latitude.sh integration for prompt management,
      tracing, evaluations, and datasets. Install with ``pip install "replicate-mcp-agents[latitude]"``.
"""

from replicate_mcp.cache import EvictionPolicy, ResultCache
from replicate_mcp.discovery import DiscoveryConfig, ModelDiscovery, VersionPinningMode, discover_and_register
from replicate_mcp.distributed import (
    DistributedExecutor,
    HttpWorkerTransport,
    RemoteWorkerNode,
    WorkerCircuitOpenError,
    WorkerNode,
    WorkerTransport,
)
from replicate_mcp.exceptions import (
    AuthenticationError,
    ClientError,
    NonRetryableError,
    RateLimitError,
    RetryableError,
    ServerError,
)
from replicate_mcp.plugins import (
    BasePlugin,
    ContentFilterPlugin,
    CostCapPlugin,
    PIIMaskPlugin,
    PluginRegistry,
    load_plugins,
)
from replicate_mcp.qos import AdaptiveRouter, QoSLevel, QoSPolicy, UCB1Router
from replicate_mcp.resilience import is_retryable_error
from replicate_mcp.routing import CostAwareRouter, ModelStats, RoutingDecision, RoutingWeights
from replicate_mcp.worker_circuit_breaker import WorkerCircuitBreaker, WorkerCircuitState
from replicate_mcp.sdk import (
    AgentBuilder,
    AgentContext,
    WorkflowBuilder,
    agent,
    get_workflow,
    list_workflows,
    load_workflows_file,
    register_workflow,
)
from replicate_mcp.utils.audit import AuditLogger, AuditRecord
from replicate_mcp.utils.router_state import RouterStateManager
from replicate_mcp.worker_server import WorkerHttpApp, serve_worker

# Latitude integration (optional extra)
try:
    from replicate_mcp.latitude import (
        LatitudeClient,
        LatitudeConfig,
        LatitudeEvalResult,
        LatitudePaymentRequiredError,
        LatitudePlugin,
        LatitudePrompt,
        LatitudeTrace,
    )

    HAS_LATITUDE = True
except ImportError:
    HAS_LATITUDE = False

__version__ = "0.6.0"

__all__ = [
    "__version__",
    # discovery
    "DiscoveryConfig",
    "ModelDiscovery",
    "VersionPinningMode",
    "discover_and_register",
    # sdk
    "agent",
    "AgentBuilder",
    "AgentContext",
    "WorkflowBuilder",
    "register_workflow",
    "get_workflow",
    "list_workflows",
    "load_workflows_file",
    # qos
    "QoSLevel",
    "QoSPolicy",
    "UCB1Router",
    "AdaptiveRouter",
    # plugins
    "BasePlugin",
    "PluginRegistry",
    "load_plugins",
    "PIIMaskPlugin",
    "ContentFilterPlugin",
    "CostCapPlugin",
    # distributed — local
    "WorkerNode",
    "DistributedExecutor",
    # distributed — remote / transport
    "WorkerTransport",
    "HttpWorkerTransport",
    "RemoteWorkerNode",
    "WorkerCircuitOpenError",
    # Phase 8 — worker circuit breaker (v0.8.0)
    "WorkerCircuitBreaker",
    "WorkerCircuitState",
    # worker server
    "WorkerHttpApp",
    "serve_worker",
    # Phase 5a — router state persistence
    "RouterStateManager",
    # Phase 5a — audit log + cost dashboard
    "AuditLogger",
    "AuditRecord",
    # Phase 5a — result cache
    "ResultCache",
    "EvictionPolicy",
    # Phase 6 — routing decision explanation
    "CostAwareRouter",
    "ModelStats",
    "RoutingDecision",
    "RoutingWeights",
    # Phase 6 — error classification
    "RetryableError",
    "NonRetryableError",
    "AuthenticationError",
    "RateLimitError",
    "ServerError",
    "ClientError",
    "is_retryable_error",
    # Phase 7 — Latitude integration (if installed)
    "HAS_LATITUDE",
    "LatitudeClient",
    "LatitudeConfig",
    "LatitudeEvalResult",
    "LatitudePaymentRequiredError",
    "LatitudePlugin",
    "LatitudePrompt",
    "LatitudeTrace",
]
