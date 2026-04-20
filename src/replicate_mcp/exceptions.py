"""Domain-specific exception hierarchy for Replicate MCP Agents.

All public exceptions subclass :class:`ReplicateMCPError` so callers can
use a single ``except`` clause for any framework-originated error.
"""

from __future__ import annotations


class ReplicateMCPError(Exception):
    """Base exception for the replicate-mcp package."""


# ---------------------------------------------------------------------------
# DAG / Workflow errors
# ---------------------------------------------------------------------------


class CycleDetectedError(ReplicateMCPError):
    """Raised when a cycle is found in a workflow DAG.

    Attributes:
        cycle: List of node IDs forming the cycle.
    """

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        path = " → ".join(cycle)
        super().__init__(f"Cycle detected: {path}")


class NodeNotFoundError(ReplicateMCPError):
    """Raised when referencing a node that does not exist in the workflow."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"Node '{node_id}' not found in workflow")


class WorkflowValidationError(ReplicateMCPError):
    """Raised when a workflow fails structural validation."""


# ---------------------------------------------------------------------------
# Model / Execution errors
# ---------------------------------------------------------------------------


class ModelNotFoundError(ReplicateMCPError):
    """Raised when an agent/model ID cannot be resolved."""

    def __init__(self, model_id: str, available: list[str] | None = None) -> None:
        self.model_id = model_id
        self.available = available or []
        msg = f"Model '{model_id}' not found"
        if self.available:
            msg += f". Available: {sorted(self.available)}"
        super().__init__(msg)


class ExecutionError(ReplicateMCPError):
    """Raised when a Replicate model invocation fails."""

    def __init__(self, model_id: str, cause: Exception | None = None) -> None:
        self.model_id = model_id
        self.cause = cause
        msg = f"Execution failed for model '{model_id}'"
        if cause:
            msg += f": {type(cause).__name__}: {cause}"
        super().__init__(msg)


class ExecutionTimeoutError(ExecutionError):
    """Raised when a model invocation exceeds its deadline."""


class TokenNotSetError(ReplicateMCPError):
    """Raised when the REPLICATE_API_TOKEN env var is missing."""

    def __init__(self) -> None:
        super().__init__(
            "REPLICATE_API_TOKEN environment variable is not set. "
            "See https://replicate.com/account/api-tokens"
        )


# ---------------------------------------------------------------------------
# Registry errors
# ---------------------------------------------------------------------------


class DuplicateAgentError(ReplicateMCPError):
    """Raised when registering an agent whose safe_name already exists."""

    def __init__(self, safe_name: str) -> None:
        self.safe_name = safe_name
        super().__init__(f"Agent '{safe_name}' is already registered")


class AgentNotFoundError(ReplicateMCPError):
    """Raised when looking up an agent that is not registered."""

    def __init__(self, safe_name: str) -> None:
        self.safe_name = safe_name
        super().__init__(f"Agent '{safe_name}' is not registered")


# ---------------------------------------------------------------------------
# Transform / Condition errors
# ---------------------------------------------------------------------------


class TransformNotFoundError(ReplicateMCPError):
    """Raised when a named transform is not in the registry."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Transform '{name}' not found in registry")


class ConditionNotFoundError(ReplicateMCPError):
    """Raised when a named condition is not in the registry."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Condition '{name}' not found in registry")


# ---------------------------------------------------------------------------
# Checkpoint errors
# ---------------------------------------------------------------------------


class CheckpointCorruptedError(ReplicateMCPError):
    """Raised when a checkpoint file cannot be deserialised."""

    def __init__(self, session_id: str, cause: Exception | None = None) -> None:
        self.session_id = session_id
        self.cause = cause
        msg = f"Checkpoint '{session_id}' is corrupted"
        if cause:
            msg += f": {cause}"
        super().__init__(msg)


__all__ = [
    "ReplicateMCPError",
    "CycleDetectedError",
    "NodeNotFoundError",
    "WorkflowValidationError",
    "ModelNotFoundError",
    "ExecutionError",
    "ExecutionTimeoutError",
    "TokenNotSetError",
    "DuplicateAgentError",
    "AgentNotFoundError",
    "TransformNotFoundError",
    "ConditionNotFoundError",
    "CheckpointCorruptedError",
]
