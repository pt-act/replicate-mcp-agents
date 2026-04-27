# API Reference

Complete reference for all public modules in `replicate_mcp`.

---

## Core modules

| Module | Description |
|---|---|
| [`replicate_mcp.sdk`](sdk.md) | `@agent` decorator, `AgentBuilder`, `WorkflowBuilder` |
| [`replicate_mcp.discovery`](discovery.md) | `ModelDiscovery`, `DiscoveryConfig`, `discover_and_register` |
| [`replicate_mcp.qos`](qos.md) | `UCB1Router`, `AdaptiveRouter`, `QoSPolicy`, `QoSLevel` |
| [`replicate_mcp.plugins`](plugins.md) | `BasePlugin`, `PluginRegistry`, `load_plugins` |
| [`replicate_mcp.distributed`](distributed.md) | `DistributedExecutor`, `WorkerNode`, `TaskResult` |

## Agent subsystem

| Module | Description |
|---|---|
| [`replicate_mcp.agents.registry`](agents/registry.md) | `AgentRegistry`, `AgentMetadata` |
| [`replicate_mcp.agents.execution`](agents/execution.md) | `AgentExecutor` |
| [`replicate_mcp.agents.composition`](agents/composition.md) | `AgentWorkflow`, `AgentNode`, DAG engine |
| [`replicate_mcp.agents.transforms`](agents/transforms.md) | `TransformFn`, built-in transforms |

## Routing and resilience

| Module | Description |
|---|---|
| [`replicate_mcp.routing`](routing.md) | `CostAwareRouter`, `ModelStats`, `RoutingWeights` |
| [`replicate_mcp.resilience`](resilience.md) | `CircuitBreaker`, `RetryConfig`, `with_retry` |
| [`replicate_mcp.ratelimit`](ratelimit.md) | `TokenBucket`, `RateLimiter` |

## Observability and security

| Module | Description |
|---|---|
| [`replicate_mcp.observability`](observability.md) | `Observability`, `ObservabilityConfig` |
| [`replicate_mcp.security`](security.md) | `SecretManager`, `SecretMasker` |

## Validation and DSL

| Module | Description |
|---|---|
| [`replicate_mcp.validation`](validation.md) | Pydantic v2 input models |
| [`replicate_mcp.dsl`](dsl.md) | `SafeEvaluator`, `safe_eval` |

## Utilities

| Module | Description |
|---|---|
| [`replicate_mcp.utils.checkpointing`](utils/checkpointing.md) | `CheckpointManager` |
| [`replicate_mcp.utils.logging`](utils/logging.md) | `configure_logging`, `get_logger` |
| [`replicate_mcp.utils.telemetry`](utils/telemetry.md) | `TelemetryTracker` |
| [`replicate_mcp.exceptions`](exceptions.md) | All exception classes |
| [`replicate_mcp.interfaces`](interfaces.md) | Protocol ABCs |