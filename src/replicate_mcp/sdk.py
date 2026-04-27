"""Fluent Python SDK for defining and invoking Replicate-backed agents.

Sprint S9 — Differentiation.  Exposes a decorator-based API that wraps
the registry and execution machinery behind ergonomic, IDE-friendly
syntax.

Key features:

* :func:`agent` decorator — declaratively define agents with metadata.
* :class:`AgentBuilder` — method-chaining builder for programmatic setup.
* :class:`WorkflowBuilder` — fluent workflow construction (sequential
  pipelines and branching).
* :class:`AgentContext` — context-manager scoped registry for tests.

Design (see ADR-006):
    - The decorator approach is purely declarative — no runtime side
      effects happen until the agent is *executed* by an
      :class:`~replicate_mcp.agents.execution.AgentExecutor`.
    - Builders follow the *named parameter* style so call-sites read
      like natural English without sacrificing type safety.
    - ``AgentContext`` ensures isolated registries in tests.

Usage::

    from replicate_mcp.sdk import agent, AgentBuilder, WorkflowBuilder

    @agent(
        model="meta/llama-3-8b-instruct",
        description="Fast chat model",
        tags=["chat", "llama"],
    )
    def chat(prompt: str) -> dict:
        return {"prompt": prompt}

    # Fluent builder:
    spec = (
        AgentBuilder("my_agent")
        .model("mistral/mixtral-8x7b-instruct")
        .description("High-quality instruction model")
        .tag("chat")
        .tag("mistral")
        .streaming(True)
        .estimated_cost(0.002)
        .build()
    )
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from replicate_mcp.agents.registry import AgentMetadata, AgentRegistry
from replicate_mcp.exceptions import ReplicateMCPError

logger = logging.getLogger(__name__)

# Module-level default registry used by the @agent decorator.
# Lazy-initialized to avoid mutable global state at import time (§03 fix).
_default_registry: AgentRegistry | None = None

# Module-level workflow registry used by register_workflow() and the CLI.
# Lazy-initialized to avoid mutable global state at import time (§03 fix).
_workflow_registry: dict[str, WorkflowSpec] | None = None


def _ensure_default_registry() -> AgentRegistry:
    """Lazy-initialize and return the module-level default registry."""
    global _default_registry  # noqa: PLW0603
    if _default_registry is None:
        _default_registry = AgentRegistry()
    return _default_registry


def _ensure_workflow_registry() -> dict[str, WorkflowSpec]:
    """Lazy-initialize and return the module-level workflow registry."""
    global _workflow_registry  # noqa: PLW0603
    if _workflow_registry is None:
        _workflow_registry = {}
    return _workflow_registry


def get_default_registry() -> AgentRegistry:
    """Return the module-level default :class:`AgentRegistry`.

    All agents registered with the :func:`agent` decorator land here
    unless an :class:`AgentContext` is active.

    The registry is created lazily on first access to avoid mutable
    global state at import time.
    """
    return _ensure_default_registry()


def reset_default_registry() -> None:
    """Clear the default agent registry.

    Useful in tests where each test case needs a clean slate.
    The registry will be recreated on next access.
    """
    global _default_registry  # noqa: PLW0603
    _default_registry = None


def reset_workflow_registry() -> None:
    """Clear the module-level workflow registry.

    Primarily useful in tests where each test case needs a clean slate.
    The registry will be recreated on next access.
    """
    global _workflow_registry  # noqa: PLW0603
    _workflow_registry = None


def register_workflow(spec: WorkflowSpec) -> WorkflowSpec:
    """Register a :class:`WorkflowSpec` in the module-level workflow registry.

    The workflow can then be executed by name via the CLI
    ``workflows run <name>`` command.

    Args:
        spec: The :class:`WorkflowSpec` to register.

    Returns:
        The registered spec (allows chaining).

    Example::

        wf = (
            WorkflowBuilder("research")
            .then("searcher")
            .then("analyst")
            .build()
        )
        register_workflow(wf)
        # Now available as: replicate-agent workflows run research
    """
    _ensure_workflow_registry()[spec.name] = spec
    return spec


def get_workflow(name: str) -> WorkflowSpec | None:
    """Return the registered :class:`WorkflowSpec` for *name*, or ``None``."""
    return _ensure_workflow_registry().get(name)


def list_workflows() -> dict[str, WorkflowSpec]:
    """Return a snapshot of the module-level workflow registry."""
    return dict(_ensure_workflow_registry())


def load_workflows_file(path: str | Path) -> int:
    """Parse a YAML workflow definition file and register all workflows.

    The file must contain a top-level ``workflows`` key with a list of
    workflow definitions.  Each definition maps directly to the
    :class:`WorkflowBuilder` API:

    .. code-block:: yaml

        workflows:
          - name: research-pipeline
            description: "Research, summarise, and classify"
            steps:
              - agent: web_searcher
                input_map: {query: user_query}
              - agent: summariser
                input_map: {text: output}
              - agent: classifier
                condition: "len(output) > 100"
                input_map: {label: output}

    Each *step* has:
    - ``agent`` (required) — the ``safe_name`` of the registered agent.
    - ``input_map`` (optional) — ``dict[str, str]`` mapping this step's
      input keys to keys from the previous step's output (or the initial
      workflow input).
    - ``condition`` (optional) — a :mod:`replicate_mcp.dsl`-compatible
      expression; the step is skipped when it evaluates to ``False``.

    Args:
        path: Path to the YAML file.

    Returns:
        Number of workflows successfully loaded and registered.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML structure is invalid.
    """
    import yaml  # type: ignore[import-untyped]  # noqa: PLC0415

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Could not parse YAML workflow file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(
            f"Workflow file {path} must contain a YAML mapping at the top level"
        )

    workflow_defs = raw.get("workflows", [])
    if not isinstance(workflow_defs, list):
        raise ValueError(
            f"'workflows' key in {path} must be a list, got {type(workflow_defs).__name__}"
        )

    loaded = 0
    for idx, wf_def in enumerate(workflow_defs):
        if not isinstance(wf_def, dict):
            logger.warning("Skipping workflow at index %d — not a dict", idx)
            continue

        name = wf_def.get("name", "").strip()
        if not name:
            logger.warning(
                "Skipping workflow at index %d — missing or empty 'name'", idx
            )
            continue

        description = str(wf_def.get("description", ""))
        steps_raw = wf_def.get("steps", [])

        if not isinstance(steps_raw, list) or not steps_raw:
            logger.warning(
                "Skipping workflow %r — 'steps' must be a non-empty list", name
            )
            continue

        try:
            builder = WorkflowBuilder(name).description(description)
            for step in steps_raw:
                if not isinstance(step, dict):
                    raise ValueError(f"Step must be a dict, got {type(step).__name__}")
                agent_name = step.get("agent", "").strip()
                if not agent_name:
                    raise ValueError("Each step must have a non-empty 'agent' key")
                input_map: dict[str, str] = step.get("input_map") or {}
                condition: str | None = step.get("condition") or None
                builder.then(
                    agent_name,
                    input_map={str(k): str(v) for k, v in input_map.items()},
                    condition=condition,
                )
            spec = builder.build()
            register_workflow(spec)
            loaded += 1
            logger.debug("Loaded workflow %r from %s", name, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load workflow %r from %s: %s", name, path, exc)

    logger.info("Loaded %d workflow(s) from %s", loaded, path)
    return loaded


# ---------------------------------------------------------------------------
# @agent decorator
# ---------------------------------------------------------------------------


def agent(
    func: Callable[..., Any] | None = None,
    *,
    model: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    supports_streaming: bool = False,
    estimated_cost: float | None = None,
    input_schema: dict[str, Any] | None = None,
    registry: AgentRegistry | None = None,
) -> Any:  # noqa: ANN401
    """Decorator that registers a function as a Replicate-backed agent.

    Can be used bare (``@agent``) or with keyword arguments
    (``@agent(model="owner/name")``).

    Args:
        func:              The decorated callable.  When the decorator is
                           used *with* arguments, ``func`` is ``None``.
        model:             Replicate model path (``"owner/model"``).
                           Defaults to the function name.
        description:       Human-readable description for MCP tool listing.
                           Defaults to the function's docstring.
        tags:              Arbitrary labels for filtering.
        supports_streaming: Whether the model supports streaming output.
        estimated_cost:    Estimated USD cost per invocation.
        input_schema:      JSON Schema dict for input validation.
        registry:          Target registry; defaults to the module-level
                           default registry.

    Returns:
        The original callable, unchanged.  The decorator is applied
        for its *side-effect* of registering the agent.

    Examples::

        @agent
        def llama(prompt: str) -> dict:
            \"\"\"Fast chat completion.\"\"\"
            return {"prompt": prompt}

        @agent(model="meta/llama-3", tags=["chat"])
        def chat(prompt: str) -> dict:
            return {"prompt": prompt}
    """
    def _register(fn: Callable[..., Any]) -> Callable[..., Any]:
        safe_name = fn.__name__
        resolved_description = description or fn.__doc__ or f"Agent: {safe_name}"
        resolved_model = model or safe_name
        # Use lazy initialization to avoid mutable global state at import time
        reg = registry or _ensure_default_registry()

        metadata = AgentMetadata(
            safe_name=safe_name,
            description=resolved_description.strip(),
            model=resolved_model,
            tags=tags or [],
            supports_streaming=supports_streaming,
            estimated_cost=estimated_cost,
            input_schema=input_schema or {},
        )
        reg.register_or_update(metadata)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            return fn(*args, **kwargs)

        wrapper.__agent_metadata__ = metadata  # type: ignore[attr-defined]
        return wrapper

    if func is not None:
        # Used as @agent without arguments
        return _register(func)

    # Used as @agent(...) with arguments
    return _register


# ---------------------------------------------------------------------------
# AgentBuilder
# ---------------------------------------------------------------------------


class AgentBuilder:
    """Fluent builder for constructing :class:`AgentMetadata` objects.

    All setter methods return ``self`` for method chaining.

    Args:
        safe_name: Unique agent identifier (used as MCP tool name).

    Example::

        spec = (
            AgentBuilder("summariser")
            .model("mistral/mixtral-8x7b-instruct")
            .description("Summarise long documents")
            .tag("nlp")
            .streaming(True)
            .estimated_cost(0.003)
            .build()
        )
    """

    def __init__(self, safe_name: str) -> None:
        if not safe_name:
            raise ValueError("safe_name must be a non-empty string")
        self._safe_name = safe_name
        self._model: str | None = None
        self._description: str = f"Agent: {safe_name}"
        self._tags: list[str] = []
        self._streaming: bool = False
        self._estimated_cost: float | None = None
        self._avg_latency_ms: int | None = None
        self._input_schema: dict[str, Any] = {}

    # ---- setters (fluent) ----

    def model(self, replicate_model: str) -> AgentBuilder:
        """Set the Replicate model path (``owner/model``)."""
        self._model = replicate_model
        return self

    def description(self, text: str) -> AgentBuilder:
        """Set the human-readable description."""
        self._description = text
        return self

    def tag(self, *tags: str) -> AgentBuilder:
        """Append one or more tags."""
        self._tags.extend(tags)
        return self

    def streaming(self, enabled: bool = True) -> AgentBuilder:
        """Enable or disable streaming output."""
        self._streaming = enabled
        return self

    def estimated_cost(self, usd: float) -> AgentBuilder:
        """Set the estimated cost per invocation in USD."""
        self._estimated_cost = usd
        return self

    def avg_latency(self, ms: int) -> AgentBuilder:
        """Set the expected average latency in milliseconds."""
        self._avg_latency_ms = ms
        return self

    def input_schema(self, schema: dict[str, Any]) -> AgentBuilder:
        """Set the JSON Schema for input validation."""
        self._input_schema = schema
        return self

    # ---- terminal ----

    def build(self) -> AgentMetadata:
        """Build and return the :class:`AgentMetadata` object.

        Does *not* register the agent; use :meth:`register` for that.
        """
        return AgentMetadata(
            safe_name=self._safe_name,
            description=self._description,
            model=self._model,
            tags=list(self._tags),
            supports_streaming=self._streaming,
            estimated_cost=self._estimated_cost,
            avg_latency_ms=self._avg_latency_ms,
            input_schema=self._input_schema,
        )

    def register(self, registry: AgentRegistry | None = None) -> AgentMetadata:
        """Build the metadata and register it in *registry*.

        Args:
            registry: Target registry.  Uses the module-level default
                      if ``None``.

        Returns:
            The registered :class:`AgentMetadata`.
        """
        meta = self.build()
        # Use lazy initialization to avoid mutable global state at import time
        reg = registry or _ensure_default_registry()
        reg.register_or_update(meta)
        return meta


# ---------------------------------------------------------------------------
# WorkflowBuilder
# ---------------------------------------------------------------------------


@dataclass
class WorkflowStep:
    """A single step in a workflow pipeline.

    Attributes:
        agent_name:   ``safe_name`` of the agent to invoke.
        input_map:    Mapping from this step's input keys to the
                      previous step's output keys (or static values).
        condition:    Optional DSL expression that must evaluate to
                      ``True`` for the step to execute.
    """

    agent_name: str
    input_map: dict[str, str] = field(default_factory=dict)
    condition: str | None = None


class WorkflowBuilder:
    """Fluent builder for sequential agent workflows.

    Example::

        wf = (
            WorkflowBuilder("my-pipeline")
            .then("summariser", input_map={"text": "raw_document"})
            .then("classifier", condition="len(output) > 100")
            .build()
        )
    """

    def __init__(self, name: str) -> None:
        if not name:
            raise ValueError("workflow name must be a non-empty string")
        self._name = name
        self._steps: list[WorkflowStep] = []
        self._description: str = ""

    def description(self, text: str) -> WorkflowBuilder:
        """Set a human-readable workflow description."""
        self._description = text
        return self

    def then(
        self,
        agent_name: str,
        *,
        input_map: dict[str, str] | None = None,
        condition: str | None = None,
    ) -> WorkflowBuilder:
        """Append a step that invokes *agent_name*.

        Args:
            agent_name: ``safe_name`` of the agent to invoke.
            input_map:  Maps this step's inputs to previous outputs.
            condition:  DSL expression guard (skip step if ``False``).
        """
        self._steps.append(
            WorkflowStep(
                agent_name=agent_name,
                input_map=input_map or {},
                condition=condition,
            )
        )
        return self

    def build(self) -> WorkflowSpec:
        """Build and return the :class:`WorkflowSpec`."""
        if not self._steps:
            raise ReplicateMCPError("workflow must have at least one step")
        return WorkflowSpec(
            name=self._name,
            description=self._description,
            steps=list(self._steps),
        )


@dataclass
class WorkflowSpec:
    """Immutable specification of a multi-step agent workflow.

    Attributes:
        name:        Unique workflow identifier.
        description: Human-readable description.
        steps:       Ordered list of :class:`WorkflowStep` objects.
    """

    name: str
    description: str
    steps: list[WorkflowStep]

    @property
    def step_count(self) -> int:
        """Number of steps in the workflow."""
        return len(self.steps)

    @property
    def agent_names(self) -> list[str]:
        """Ordered list of agent names referenced by this workflow."""
        return [s.agent_name for s in self.steps]


# ---------------------------------------------------------------------------
# AgentContext (scoped registry for tests)
# ---------------------------------------------------------------------------


class AgentContext:
    """Context manager that isolates a temporary :class:`AgentRegistry`.

    On exit the module-level default registry is restored to its state
    before the context was entered.  This is useful for test cases that
    register agents via the ``@agent`` decorator.

    Example::

        with AgentContext() as ctx:
            @agent(registry=ctx.registry)
            def my_model(prompt: str) -> dict:
                return {"prompt": prompt}

            assert ctx.registry.has("my_model")
    """

    def __init__(self) -> None:
        self._registry = AgentRegistry()
        self._saved: AgentRegistry | None = None

    @property
    def registry(self) -> AgentRegistry:
        """The isolated registry for this context."""
        return self._registry

    def __enter__(self) -> AgentContext:
        global _default_registry  # noqa: PLW0603
        self._saved = _default_registry
        _default_registry = self._registry
        return self

    def __exit__(self, *_: object) -> None:
        global _default_registry  # noqa: PLW0603
        if self._saved is not None:
            _default_registry = self._saved


__all__ = [
    "agent",
    "get_default_registry",
    "reset_default_registry",
    "register_workflow",
    "reset_workflow_registry",
    "get_workflow",
    "list_workflows",
    "load_workflows_file",
    "AgentBuilder",
    "WorkflowBuilder",
    "WorkflowSpec",
    "WorkflowStep",
    "AgentContext",
]
