"""Pydantic v2 validation models for all external-facing inputs.

Sprint S5 — Hardening.  All data that enters the system from untrusted
sources (CLI flags, MCP tool calls, YAML config files, API requests)
must pass through one of the models in this module before touching any
internal logic.

Why Pydantic v2:
    • Automatic JSON Schema generation (powers MCP ``input_schema``)
    • Strict mode catches type confusion bugs at the boundary
    • ``model_validator`` / ``field_validator`` keep sanitisation
      co-located with the data structure

Usage::

    from replicate_mcp.validation import AgentInputModel, WorkflowInputModel

    validated = AgentInputModel.model_validate(raw_dict)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_\-]{0,63}$")
_MODEL_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-.:]+$")

NonEmptyStr = Annotated[str, Field(min_length=1, max_length=4096)]


# ---------------------------------------------------------------------------
# Agent invocation
# ---------------------------------------------------------------------------


class AgentInputModel(BaseModel):
    """Validated input for a single agent invocation.

    Consumed by :class:`~replicate_mcp.agents.execution.AgentExecutor`.
    """

    model_config = ConfigDict(strict=False, extra="forbid")

    agent_id: str = Field(
        description="The ``safe_name`` of the agent to invoke.",
        min_length=1,
        max_length=64,
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Key/value pairs forwarded to the Replicate model.",
    )
    stream: bool = Field(
        default=False,
        description="Request streaming output when ``True``.",
    )
    timeout_s: float = Field(
        default=120.0,
        gt=0,
        le=600,
        description="Maximum seconds to wait for a response.",
    )

    @field_validator("agent_id")
    @classmethod
    def _agent_id_safe(cls, v: str) -> str:
        if not _SAFE_NAME_RE.match(v):
            raise ValueError(
                f"agent_id '{v}' must match {_SAFE_NAME_RE.pattern}"
            )
        return v

    @field_validator("payload")
    @classmethod
    def _payload_not_too_large(cls, v: dict[str, Any]) -> dict[str, Any]:
        import json

        serialised = json.dumps(v)
        if len(serialised) > 1_048_576:  # 1 MiB
            raise ValueError("payload exceeds 1 MiB — split into smaller chunks")
        return v


# ---------------------------------------------------------------------------
# Workflow invocation
# ---------------------------------------------------------------------------


class WorkflowInputModel(BaseModel):
    """Validated input for a multi-agent workflow execution."""

    model_config = ConfigDict(strict=False, extra="forbid")

    workflow_name: str = Field(min_length=1, max_length=128)
    initial_input: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False
    checkpoint_dir: Path | None = Field(
        default=None,
        description="Directory for checkpoint files. Created if absent.",
    )
    resume_from: str | None = Field(
        default=None,
        description="Node ID to resume from; prior nodes are skipped.",
    )
    max_concurrency: int = Field(
        default=4,
        ge=1,
        le=64,
        description="Maximum concurrent node executions per level.",
    )

    @model_validator(mode="after")
    def _resume_requires_checkpoint(self) -> WorkflowInputModel:
        if self.resume_from and not self.checkpoint_dir:
            raise ValueError(
                "'resume_from' requires 'checkpoint_dir' to be set so the "
                "previous checkpoint can be loaded."
            )
        return self


# ---------------------------------------------------------------------------
# Agent metadata (registration)
# ---------------------------------------------------------------------------


class AgentMetadataModel(BaseModel):
    """Validated schema for registering an agent.

    Mirrors :class:`~replicate_mcp.agents.registry.AgentMetadata` but
    with full validation so external sources (YAML, REST API) can't
    inject malformed data.
    """

    model_config = ConfigDict(strict=False, extra="ignore")

    safe_name: str = Field(
        min_length=1,
        max_length=64,
        description="Unique MCP tool name for this agent.",
    )
    description: str = Field(
        min_length=1,
        max_length=1024,
        description="Human-readable description shown in MCP clients.",
    )
    model: str | None = Field(
        default=None,
        description="Full Replicate model path (``owner/model[:version]``).",
    )
    input_schema: dict[str, Any] = Field(default_factory=dict)
    supports_streaming: bool = False
    estimated_cost: float | None = Field(
        default=None,
        ge=0,
        description="Estimated cost in USD per invocation.",
    )
    avg_latency_ms: int | None = Field(
        default=None,
        ge=0,
        description="Expected latency in milliseconds.",
    )
    tags: list[str] = Field(default_factory=list)

    @field_validator("safe_name")
    @classmethod
    def _safe_name_valid(cls, v: str) -> str:
        if not _SAFE_NAME_RE.match(v):
            raise ValueError(f"safe_name '{v}' must match {_SAFE_NAME_RE.pattern}")
        return v

    @field_validator("model")
    @classmethod
    def _model_path_valid(cls, v: str | None) -> str | None:
        if v is not None and not _MODEL_PATH_RE.match(v):
            raise ValueError(
                f"model '{v}' must be in 'owner/model' or 'owner/model:version' format"
            )
        return v

    @field_validator("tags")
    @classmethod
    def _tags_valid(cls, v: list[str]) -> list[str]:
        for tag in v:
            if len(tag) > 64 or not tag:
                raise ValueError(f"Invalid tag '{tag}' — must be 1–64 characters")
        return v


# ---------------------------------------------------------------------------
# Server / transport config
# ---------------------------------------------------------------------------


class ServerConfigModel(BaseModel):
    """Validated configuration for the MCP server process."""

    model_config = ConfigDict(strict=False, extra="ignore")

    transport: str = Field(
        default="stdio",
        description="Transport layer: 'stdio' or 'sse'.",
    )
    log_level: str = Field(
        default="INFO",
        description="Root log level (DEBUG, INFO, WARNING, ERROR).",
    )
    max_concurrency: int = Field(
        default=8,
        ge=1,
        le=256,
        description="Max concurrent tool-call handlers.",
    )
    enable_telemetry: bool = Field(
        default=True,
        description="Emit OpenTelemetry spans and metrics when ``True``.",
    )

    @field_validator("transport")
    @classmethod
    def _transport_valid(cls, v: str) -> str:
        allowed = {"stdio", "sse"}
        if v not in allowed:
            raise ValueError(f"transport must be one of {sorted(allowed)}, got '{v}'")
        return v

    @field_validator("log_level")
    @classmethod
    def _log_level_valid(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got '{v}'")
        return upper


# ---------------------------------------------------------------------------
# DSL expression (Sprint S6)
# ---------------------------------------------------------------------------


class DSLExpressionModel(BaseModel):
    """Validated input for a safe DSL expression string."""

    model_config = ConfigDict(strict=True)

    expression: str = Field(
        min_length=1,
        max_length=512,
        description="A restricted Python-like expression evaluated safely.",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Variables available to the expression at evaluation time.",
    )


__all__ = [
    "AgentInputModel",
    "WorkflowInputModel",
    "AgentMetadataModel",
    "ServerConfigModel",
    "DSLExpressionModel",
    "NonEmptyStr",
]
