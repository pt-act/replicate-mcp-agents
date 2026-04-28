"""Latitude (latitude.sh) integration for prompt management, tracing, and evaluation.

Provides comprehensive integration with Latitude for:
    * **Prompt Management** — Fetch, create, and version prompts
    * **Prompt Execution** — Run prompts via Latitude API with streaming support
    * **Conversations** — Multi-turn chat with conversation history
    * **Version Control** — Draft versions, publishing, and A/B testing
    * **Tracing** — Automatic tracing of agent executions with full context

API Base URL: https://gateway.latitude.so/api/v3

Environment Variables:
    LATITUDE_API_KEY       — Your Latitude API key (required)
    LATITUDE_PROJECT_ID    — Default project ID (v1 - numeric)
    LATITUDE_PROJECT_SLUG  — Default project slug (v2 - e.g., "replicate-mcp-agents")

API Versions:
    v1 (legacy): Uses numeric project_id (e.g., 32129)
    v2 (current): Uses project slug (e.g., "replicate-mcp-agents")
    The client auto-detects based on which env var is set (slug takes precedence).

Key API Endpoints Implemented:
    * GET /projects/{projectId}/versions/{versionUuid}/documents/{path} — Fetch prompt
    * POST /projects/{projectId}/versions/{versionUuid}/documents/run — Execute prompt
    * POST /conversations/{conversationUuid}/chat — Continue conversation
    * GET /conversations/{conversationUuid} — Get conversation history
    * POST /projects/{projectId}/versions — Create new version
    * POST /projects/{projectId}/versions/{versionUuid}/publish — Publish version
    * POST /traces — Submit execution traces (OTLP format)

Design (see ADR-009):
    - Zero-overhead when disabled: all methods no-op if no API key
    - Lazy initialization: client connects on first use, not import
    - Async-first: all I/O is async to fit the existing async architecture
    - Pluggable: works as middleware, plugin, or direct integration
    - OTEL-compatible: traces integrate with existing observability
    - Graceful degradation: API failures logged but never break execution

Usage::

    from replicate_mcp.latitude import LatitudeClient, LatitudeConfig

    # Direct client usage
    config = LatitudeConfig()  # From env vars
    client = LatitudeClient(config)

    async with client:
        # Fetch a prompt from live/production version
        prompt = await client.get_prompt("path/to/document", version_uuid="live")

        # Run the prompt
        result = await client.run_prompt(
            "path/to/document",
            parameters={"name": "World"},
            version_uuid="live"
        )

        # Continue conversation
        chat_result = await client.chat(
            result["uuid"],
            messages=[{"role": "user", "content": [{"type": "text", "text": "Hello!"}]}]
        )

    # Automatic tracing via plugin
    from replicate_mcp.plugins import PluginRegistry
    from replicate_mcp.latitude import LatitudePlugin

    registry = PluginRegistry()
    registry.load(LatitudePlugin(config))
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# Lazy import for optional httpx dependency
try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from replicate_mcp.exceptions import ReplicateMCPError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class LatitudeConfig:
    """Configuration for Latitude integration.

    Supports both v1 (project_id) and v2 (project_slug) APIs.

    Attributes:
        api_key:        Latitude API key. Falls back to LATITUDE_API_KEY env var.
        project_id:     Default project ID (v1). Falls back to LATITUDE_PROJECT_ID env var.
        project_slug:   Default project slug (v2). Falls back to LATITUDE_PROJECT_SLUG env var.
        base_url:       API base URL. Default: https://gateway.latitude.so/api/v3
        timeout_s:      Request timeout in seconds.
        enable_tracing:     Emit traces for agent executions.
        enable_prompt_caching: Cache fetched prompts in memory.
        cache_ttl_s:    Prompt cache TTL in seconds.
    """

    api_key: str | None = None
    project_id: str | None = None  # v1: numeric ID
    project_slug: str | None = None  # v2: project name/slug
    base_url: str = "https://gateway.latitude.so/api/v3"
    timeout_s: float = 30.0
    enable_tracing: bool = True
    enable_prompt_caching: bool = True
    cache_ttl_s: float = 300.0

    def __post_init__(self) -> None:
        # Fall back to environment variables
        if self.api_key is None:
            self.api_key = os.environ.get("LATITUDE_API_KEY")
        if self.project_id is None:
            self.project_id = os.environ.get("LATITUDE_PROJECT_ID")
        if self.project_slug is None:
            self.project_slug = os.environ.get("LATITUDE_PROJECT_SLUG")

    @property
    def is_configured(self) -> bool:
        """Return True if api_key and project identifier (id or slug) are set."""
        return bool(self.api_key and (self.project_id or self.project_slug))

    def get_project_id(self, override: str | None = None) -> str:
        """Get the project identifier to use for API calls.

        v2 uses project_slug (e.g., 'replicate-mcp-agents').
        v1 uses project_id (numeric or string).

        Prefers slug if available (v2), falls back to id (v1).

        Args:
            override: Optional override value.

        Returns:
            Project identifier for URL construction.

        Raises:
            LatitudeNotConfiguredError: If no project identifier is available.
        """
        if override:
            return override
        if self.project_slug:
            return self.project_slug
        if self.project_id:
            return self.project_id
        raise LatitudeNotConfiguredError(
            "No project identifier configured. Set LATITUDE_PROJECT_SLUG (v2) "
            "or LATITUDE_PROJECT_ID (v1) environment variable."
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LatitudeError(ReplicateMCPError):
    """Base exception for Latitude integration errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LatitudeNotConfiguredError(LatitudeError):
    """Raised when Latitude is used without proper configuration."""

    def __init__(self, message: str | None = None) -> None:
        if message is None:
            message = (
                "Latitude not configured. Set LATITUDE_API_KEY and LATITUDE_PROJECT_ID "
                "environment variables or pass to LatitudeConfig."
            )
        super().__init__(message)


class LatitudeAPIError(LatitudeError):
    """Raised when Latitude API returns an error response."""

    def __init__(self, message: str, status_code: int, response_body: str = "") -> None:
        super().__init__(message, status_code)
        self.response_body = response_body


class LatitudePaymentRequiredError(LatitudeAPIError):
    """Raised when Latitude trial has ended or payment is required."""

    def __init__(self, response_body: str = "") -> None:
        super().__init__(
            "Latitude trial ended or payment required. Please upgrade your account.",
            402,
            response_body,
        )


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class LatitudePrompt:
    """A fetched prompt from Latitude.

    Attributes:
        id:           Prompt ID.
        name:         Prompt name/path.
        version:      Version identifier (commit SHA, tag, or "latest").
        content:      The prompt template/content.
        config:       Provider-specific config (temperature, etc.).
        metadata:     Additional metadata from Latitude.
    """

    id: str
    name: str
    version: str
    content: str
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LatitudeTrace:
    """An active or completed trace for an agent execution.

    Traces capture the full context of an execution including inputs,
    outputs, latency, cost, and model information.
    """

    id: str
    name: str
    agent_id: str
    model: str | None = None
    start_time: float = field(default_factory=lambda: __import__("time").time())
    end_time: float | None = None
    input_data: dict[str, Any] = field(default_factory=dict, repr=False)
    output_data: dict[str, Any] = field(default_factory=dict, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    latency_ms: float | None = None
    success: bool = True
    error_message: str | None = None

    def record_result(
        self,
        output: dict[str, Any],
        latency_ms: float | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Record the result of the execution."""
        self.output_data = output
        self.end_time = __import__("time").time()
        if latency_ms is not None:
            self.latency_ms = latency_ms
        if cost_usd is not None:
            self.cost_usd = cost_usd

    def record_error(self, error: Exception) -> None:
        """Record an error that occurred during execution."""
        self.success = False
        self.error_message = str(error)
        end_time = __import__("time").time()
        self.end_time = end_time
        if self.latency_ms is None:
            start = self.start_time
            if start is not None:
                self.latency_ms = (end_time - start) * 1000

    def to_api_payload(self) -> dict[str, Any]:
        """Convert to the payload format expected by Latitude API."""
        return {
            "id": self.id,
            "name": self.name,
            "agentId": self.agent_id,
            "model": self.model,
            "startTime": self.start_time,
            "endTime": self.end_time,
            "input": self.input_data,
            "output": self.output_data,
            "metadata": self.metadata,
            "cost": self.cost_usd,
            "latency": self.latency_ms,
            "success": self.success,
            "error": self.error_message,
        }


@dataclass
class LatitudeEvalResult:
    """Result of running an evaluation on an agent output."""

    eval_id: str
    trace_id: str
    score: float | None = None
    passed: bool | None = None
    feedback: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core Client
# ---------------------------------------------------------------------------


class LatitudeClient:
    """Async client for Latitude API.

    Handles prompt fetching, tracing, evaluations, and dataset exports.
    Automatically handles authentication, retries, and error handling.

    Args:
        config: LatitudeConfig instance. Uses env vars if not provided.
    """

    def __init__(self, config: LatitudeConfig | None = None) -> None:
        if not HAS_HTTPX:
            raise ImportError(
                "Latitude integration requires httpx. "
                "Install with: pip install replicate-mcp-agents[latitude]"
            )

        self.config = config or LatitudeConfig()
        self._client: httpx.AsyncClient | None = None
        self._prompt_cache: dict[str, tuple[LatitudePrompt, float]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client."""
        if self._client is None or self._client.is_closed:
            if not self.config.is_configured:
                raise LatitudeNotConfiguredError()

            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout_s),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def validate_connection(self) -> dict[str, Any]:
        """Validate API connectivity and return diagnostics.

        Returns:
            Dict with 'success', 'status', 'endpoint', and 'response' keys.
            Useful for debugging API configuration issues.
        """
        if not self.config.is_configured:
            return {"success": False, "error": "Not configured", "details": "LATITUDE_API_KEY or LATITUDE_PROJECT_ID missing"}

        client = await self._get_client()

        # Skip health check - try traces endpoint directly
        try:
            test_resp = await client.post(
                "/traces",
                json={"name": "validation-test", "agentId": "validation"}
            )
            return {
                "success": test_resp.status_code < 400,
                "status": test_resp.status_code,
                "endpoint": "/traces",
                "response": test_resp.text[:200] if test_resp.status_code >= 400 else "OK"
            }
        except Exception as e:
            return {"success": False, "error": str(e), "endpoint": "/traces"}

    async def __aenter__(self) -> LatitudeClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # ---- Prompt Management ----

    async def get_prompt(
        self,
        path: str,
        version_uuid: str = "live",
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> LatitudePrompt:
        """Fetch a prompt from Latitude.

        Per API docs: GET /projects/{projectId}/versions/{versionUuid}/documents/{path}

        Args:
            path:         Prompt path (e.g., "path/to/document").
            version_uuid: Version UUID (defaults to "live" for production).
            project_id:   Override the default project ID.

        Returns:
            LatitudePrompt instance with content and metadata.

        Raises:
            LatitudeAPIError: If the API returns an error.
            LatitudeNotConfiguredError: If client is not configured.
        """
        cache_key = f"{project_id or self.config.project_id}:{path}:{version_uuid}"

        # Check cache
        if self.config.enable_prompt_caching:
            cached = self._prompt_cache.get(cache_key)
            if cached:
                prompt, cached_at = cached
                if (__import__("time").time() - cached_at) < self.config.cache_ttl_s:
                    return prompt

        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        # Correct endpoint per API docs
        url = f"/projects/{pid}/versions/{version_uuid}/documents/{path}"

        response = await client.get(url)

        if response.status_code == 404:
            raise LatitudeAPIError(
                f"Prompt '{path}' not found (version: {version_uuid})",
                status_code=404,
            )
        response.raise_for_status()

        data = response.json()
        # Map API response to our model
        prompt = LatitudePrompt(
            id=data.get("id", ""),
            name=data.get("path", path),
            version=data.get("commitId", version_uuid),
            content=data.get("content", ""),
            config=data.get("config", {}),
            metadata={
                "documentUuid": data.get("documentUuid", ""),
                "contentHash": data.get("contentHash", ""),
                "createdAt": data.get("createdAt", ""),
                "updatedAt": data.get("updatedAt", ""),
                "resolvedContent": data.get("resolvedContent", ""),
            },
        )

        # Cache result
        if self.config.enable_prompt_caching:
            self._prompt_cache[cache_key] = (prompt, __import__("time").time())

        return prompt

    async def run_prompt(
        self,
        path: str,
        parameters: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        stream: bool = False,
        background: bool = False,
        version_uuid: str = "live",
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> dict[str, Any]:
        """Execute a prompt via Latitude API.

        Per API docs: POST /projects/{projectId}/versions/{versionUuid}/documents/run

        Args:
            path:         Prompt path to run.
            parameters:   Template parameters for the prompt.
            messages:     Additional messages to append (PromptL format).
            stream:       Whether to stream response as SSE.
            background:   Whether to enqueue for background processing.
            version_uuid: Version UUID (defaults to "live").
            project_id:   Override the default project ID.

        Returns:
            Response dict with conversation UUID, usage, cost, etc.
        """
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        url = f"/projects/{pid}/versions/{version_uuid}/documents/run"

        payload: dict[str, Any] = {"path": path}
        if parameters:
            payload["parameters"] = parameters
        if messages:
            payload["messages"] = messages
        if stream:
            payload["stream"] = stream
        if background:
            payload["background"] = background

        response = await client.post(url, json=payload)

        if response.status_code == 404:
            raise LatitudeAPIError(
                f"Prompt '{path}' not found (version: {version_uuid})",
                status_code=404,
            )
        response.raise_for_status()

        result: dict[str, Any] = response.json()
        return result

    async def get_or_create_prompt(
        self,
        path: str,
        content: str = "",
        version_uuid: str = "live",
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> LatitudePrompt:
        """Get existing prompt or create it if not found.

        Per API docs: POST /projects/{projectId}/versions/{versionUuid}/documents/get-or-create

        Args:
            path:         Prompt path.
            content:      Content if creating new prompt.
            version_uuid: Version UUID (defaults to "live").
            project_id:   Override the default project ID.

        Returns:
            LatitudePrompt instance.
        """
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        url = f"/projects/{pid}/versions/{version_uuid}/documents/get-or-create"

        response = await client.post(url, json={"path": path, "prompt": content})
        response.raise_for_status()

        data = response.json()
        return LatitudePrompt(
            id=data.get("id", ""),
            name=data.get("path", path),
            version=data.get("commitId", version_uuid),
            content=data.get("content", ""),
            config=data.get("config", {}),
            metadata={
                "documentUuid": data.get("documentUuid", ""),
                "contentHash": data.get("contentHash", ""),
                "createdAt": data.get("createdAt", ""),
                "updatedAt": data.get("updatedAt", ""),
            },
        )

    async def create_or_update_prompt(
        self,
        path: str,
        content: str,
        force: bool = False,
        version_uuid: str = "live",
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> LatitudePrompt:
        """Create new prompt or update existing one.

        Per API docs: POST /projects/{projectId}/versions/{versionUuid}/documents/create-or-update

        Args:
            path:         Prompt path.
            content:      Prompt content.
            force:        Allow modifying live/merged commits (use with caution).
            version_uuid: Version UUID (defaults to "live").
            project_id:   Override the default project ID.

        Returns:
            LatitudePrompt instance.
        """
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        url = f"/projects/{pid}/versions/{version_uuid}/documents/create-or-update"

        response = await client.post(url, json={"path": path, "prompt": content, "force": force})
        response.raise_for_status()

        data = response.json()
        return LatitudePrompt(
            id=data.get("id", ""),
            name=data.get("path", path),
            version=data.get("commitId", version_uuid),
            content=data.get("content", ""),
            config=data.get("config", {}),
            metadata={
                "documentUuid": data.get("documentUuid", ""),
                "contentHash": data.get("contentHash", ""),
                "createdAt": data.get("createdAt", ""),
                "updatedAt": data.get("updatedAt", ""),
            },
        )

    async def create_version(
        self,
        name: str,
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> dict[str, Any]:
        """Create a new draft version (commit) for a project.

        Per API docs: POST /projects/{projectId}/versions

        Args:
            name:         Version name/title.
            project_id:   Override the default project ID.

        Returns:
            Version dict with id, uuid, status, etc.
        """
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        response = await client.post(
            f"/projects/{pid}/versions",
            json={"name": name}
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def publish_version(
        self,
        version_uuid: str,
        title: str | None = None,
        description: str | None = None,
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> dict[str, Any]:
        """Publish a draft version to make it live/production.

        Per API docs: POST /projects/{projectId}/versions/{versionUuid}/publish

        Args:
            version_uuid: UUID of the draft version to publish.
            title:        Optional updated title.
            description:  Optional release notes.
            project_id:   Override the default project ID.

        Returns:
            Published version dict.
        """
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        url = f"/projects/{pid}/versions/{version_uuid}/publish"
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        if description:
            payload["description"] = description

        response = await client.post(url, json=payload)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    # ---- Conversations ----

    async def chat(
        self,
        conversation_uuid: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
    ) -> dict[str, Any]:
        """Continue a multi-turn conversation.

        Per API docs: POST /conversations/{conversationUuid}/chat

        Args:
            conversation_uuid: UUID of the conversation.
            messages:          Messages to append (PromptL format).
            stream:            Whether to stream response as SSE.

        Returns:
            Response dict with conversation, usage, cost, etc.
        """
        client = await self._get_client()

        url = f"/conversations/{conversation_uuid}/chat"
        payload: dict[str, Any] = {"messages": messages}
        if stream:
            payload["stream"] = stream

        response = await client.post(url, json=payload)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def get_conversation(self, conversation_uuid: str) -> dict[str, Any]:
        """Retrieve conversation history.

        Per API docs: GET /conversations/{conversationUuid}

        Args:
            conversation_uuid: UUID of the conversation.

        Returns:
            Dict with uuid and conversation message array.
        """
        client = await self._get_client()

        response = await client.get(f"/conversations/{conversation_uuid}")
        if response.status_code == 404:
            raise LatitudeAPIError(
                f"Conversation '{conversation_uuid}' not found",
                status_code=404,
            )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def stop_conversation(self, conversation_uuid: str) -> None:
        """Stop an active conversation.

        Per API docs: POST /conversations/{conversationUuid}/stop

        Args:
            conversation_uuid: UUID of the conversation.
        """
        client = await self._get_client()

        response = await client.post(f"/conversations/{conversation_uuid}/stop")
        response.raise_for_status()

    # ---- Tracing ----

    def start_trace(
        self,
        name: str,
        agent_id: str,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LatitudeTrace:
        """Start a new trace for an agent execution.

        Args:
            name:         Trace name (e.g., "agent-run").
            agent_id:     Identifier for the agent being traced.
            input_data:   Input payload to the agent.
            metadata:     Additional metadata (model, version, etc.).

        Returns:
            LatitudeTrace instance. Call record_result() when complete.
        """
        import time
        import uuid

        if not self.config.enable_tracing:
            # Return a no-op trace that does nothing on record
            return LatitudeTrace(
                id="noop",
                name=name,
                agent_id=agent_id,
                metadata={"_noop": True},
            )

        return LatitudeTrace(
            id=str(uuid.uuid4()),
            name=name,
            agent_id=agent_id,
            input_data=input_data or {},
            metadata=metadata or {},
            start_time=time.time(),
        )

    async def end_trace(self, trace: LatitudeTrace) -> None:
        """End a trace and send it to Latitude.

        Args:
            trace: The LatitudeTrace to finalize and send.
        """
        if not self.config.enable_tracing or trace.id == "noop":
            return

        if trace.end_time is None:
            import time

            trace.end_time = time.time()
            if trace.latency_ms is None:
                trace.latency_ms = (trace.end_time - trace.start_time) * 1000

        client = await self._get_client()
        pid = self.config.project_id

        try:
            resp = await client.post(
                "/traces",
                json={
                    "projectId": pid,
                    **trace.to_api_payload(),
                },
            )
            if resp.status_code == 402:
                # Trial ended - log once, then disable to avoid spam
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    "Latitude trial ended or payment required. "
                    "Traces will not be sent. Please upgrade at https://latitude.sh"
                )
                # Disable tracing to avoid repeated warnings
                self.config.enable_tracing = False
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 402:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    "Latitude trial ended or payment required. "
                    "Traces will not be sent. Please upgrade at https://latitude.sh"
                )
                self.config.enable_tracing = False
            # Log but don't raise — telemetry should never break execution
            import logging
            logging.getLogger(__name__).warning(
                f"Failed to send trace to Latitude: {e}",
                exc_info=True,
            )
        except httpx.HTTPError as e:
            # Log but don't raise — telemetry should never break execution
            import logging
            logging.getLogger(__name__).warning(
                f"Failed to send trace to Latitude: {e}",
                exc_info=True,
            )

    def trace(
        self,
        name: str,
        agent_id: str,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LatitudeTraceContext:
        """Context manager for tracing an agent execution.

        Usage::

            async with client.trace("agent-run", "my-agent") as trace:
                result = await run_agent()
                trace.record_result(result)
        """
        return LatitudeTraceContext(
            self,
            self.start_trace(name, agent_id, input_data, metadata),
        )

    # ---- Evaluations ----

    async def run_eval(
        self,
        trace_id: str,
        eval_name: str,
        output: dict[str, Any],
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> LatitudeEvalResult:
        """Run an evaluation on a trace.

        Args:
            trace_id:     The trace ID to evaluate.
            eval_name:    Name of the evaluation to run.
            output:       The agent output to evaluate.
            project_id:   Override the default project ID.

        Returns:
            LatitudeEvalResult with score and feedback.
        """
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        response = await client.post(
            f"/projects/{pid}/evaluations",
            json={
                "traceId": trace_id,
                "evaluation": eval_name,
                "output": output,
            },
        )
        response.raise_for_status()

        data = response.json()
        return LatitudeEvalResult(
            eval_id=data.get("id", ""),
            trace_id=trace_id,
            score=data.get("score"),
            passed=data.get("passed"),
            feedback=data.get("feedback"),
            metadata=data.get("metadata", {}),
        )

    async def list_evals(
        self,
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> list[dict[str, Any]]:
        """List available evaluations in a project."""
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        response = await client.get(f"/projects/{pid}/evaluations")
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        evaluations: list[dict[str, Any]] = data.get("evaluations", [])
        return evaluations

    async def create_dataset(
        self,
        name: str,
        description: str = "",
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> dict[str, Any]:
        """Create a new dataset for training data."""
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        response = await client.post(
            f"/projects/{pid}/datasets",
            json={
                "name": name,
                "description": description,
            },
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def export_traces_to_dataset(
        self,
        dataset_id: str,
        trace_ids: list[str],
        project_id: str | None = None,
        project_slug: str | None = None,
    ) -> dict[str, Any]:
        """Export specific traces to a dataset.

        Args:
            dataset_id:   The target dataset ID.
            trace_ids:    List of trace IDs to export.
            project_id:   Override the default project ID.

        Returns:
            Export result with record count.
        """
        client = await self._get_client()
        pid = self.config.get_project_id(project_id or project_slug)

        response = await client.post(
            f"/projects/{pid}/datasets/{dataset_id}/traces",
            json={"traceIds": trace_ids},
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


# ---------------------------------------------------------------------------
# Trace Context Manager
# ---------------------------------------------------------------------------


class LatitudeTraceContext:
    """Async context manager for a Latitude trace.

    Automatically sends the trace to Latitude when exiting the context,
    even if an exception occurred.
    """

    def __init__(self, client: LatitudeClient, trace: LatitudeTrace) -> None:
        self.client = client
        self.trace = trace
        self._entered = False

    async def __aenter__(self) -> LatitudeTrace:
        self._entered = True
        return self.trace

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if not self._entered:
            return

        if exc_val is not None:
            self.trace.record_error(exc_val)

        await self.client.end_trace(self.trace)


# ---------------------------------------------------------------------------
# Plugin Integration
# ---------------------------------------------------------------------------

# Runtime import to avoid circular imports
try:
    from replicate_mcp.plugins.base import BasePlugin as _BasePlugin
    from replicate_mcp.plugins.base import PluginMetadata as _PluginMetadata
    _BASE_PLUGIN_AVAILABLE = True
except ImportError:
    # Define stub classes if plugin base not available
    class _BasePlugin:  # type: ignore[no-redef]
        """Stub base plugin when plugin system unavailable."""

        def __init__(self) -> None:
            pass

    class _PluginMetadata:  # type: ignore[no-redef]
        """Stub metadata when plugin system unavailable."""

        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    _BASE_PLUGIN_AVAILABLE = False


class LatitudePlugin(_BasePlugin):
    """Plugin that automatically traces all agent executions to Latitude.

    Integrates with the existing plugin system to capture:
        - Agent inputs (via on_agent_run)
        - Agent outputs (via on_agent_result)
        - Errors (via on_error)
        - Latency and cost tracking

    Usage::

        from replicate_mcp.latitude import LatitudePlugin, LatitudeConfig
        from replicate_mcp.plugins import PluginRegistry

        config = LatitudeConfig()  # From env vars
        plugin = LatitudePlugin(config)

        registry = PluginRegistry()
        registry.load(plugin)

        # All agent runs are now traced to Latitude automatically
    """

    def __init__(self, config: LatitudeConfig | None = None) -> None:
        self.config = config or LatitudeConfig()
        self._client: LatitudeClient | None = None
        self._active_traces: dict[str, LatitudeTrace] = {}

    @property
    def metadata(self) -> _PluginMetadata:
        return _PluginMetadata(
            name="latitude",
            version="1.0.0",
            description="Latitude.sh integration for prompt management and tracing",
            author="pt-act",
        )

    def setup(self) -> None:
        """Initialize the Latitude client."""
        if self.config.is_configured:
            self._client = LatitudeClient(self.config)

    def teardown(self) -> None:
        """Clean up resources."""
        # Active traces will be lost if not ended — this is acceptable
        # since the framework is shutting down
        self._client = None
        self._active_traces.clear()

    def on_agent_run(
        self,
        agent_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Start tracing this agent execution."""
        if self._client is None or not self.config.enable_tracing:
            return None

        # Generate a correlation ID for this execution
        import uuid

        correlation_id = str(uuid.uuid4())

        trace = self._client.start_trace(
            name="agent-execution",
            agent_id=agent_name,
            input_data=payload,
            metadata={"correlation_id": correlation_id},
        )

        # Store for later retrieval in on_agent_result
        self._active_traces[correlation_id] = trace

        # Add correlation ID to payload so it flows through to result
        return {**payload, "_latitude_trace_id": correlation_id}

    def on_agent_result(
        self,
        agent_name: str,
        chunks: list[dict[str, Any]],
        latency_ms: float,
    ) -> list[dict[str, Any]] | None:
        """Finalize and send the trace to Latitude."""
        if self._client is None or not self.config.enable_tracing:
            return None

        # Extract correlation ID from last chunk metadata (if present)
        correlation_id: str | None = None
        for chunk in reversed(chunks):
            if "_latitude_trace_id" in chunk:
                correlation_id = chunk.pop("_latitude_trace_id")
                break

        if correlation_id is None or correlation_id not in self._active_traces:
            # Trace not found — create a new one with what we have
            trace = self._client.start_trace(
                name="agent-execution",
                agent_id=agent_name,
            )
            trace.record_result({"chunks": chunks}, latency_ms=latency_ms)
        else:
            trace = self._active_traces.pop(correlation_id)
            trace.record_result({"chunks": chunks}, latency_ms=latency_ms)

        # Send trace asynchronously — fire and forget
        import asyncio

        asyncio.create_task(self._client.end_trace(trace))

        return None  # Don't modify chunks

    def on_error(
        self,
        agent_name: str,
        error: Exception,
    ) -> None:
        """Record error in active traces."""
        if self._client is None or not self.config.enable_tracing:
            return

        # Find and finalize any active traces for this agent
        # Note: We can't easily correlate without the trace ID,
        # so we use a best-effort approach
        for trace in list(self._active_traces.values()):
            if trace.agent_id == agent_name and trace.end_time is None:
                trace.record_error(error)
                import asyncio

                asyncio.create_task(self._client.end_trace(trace))


# ---------------------------------------------------------------------------
# Integration with Observability
# ---------------------------------------------------------------------------


class LatitudeObservabilityBridge:
    """Bridge to integrate Latitude traces with existing OpenTelemetry observability.

    This allows Latitude traces to appear as OTEL spans and vice versa,
    providing unified telemetry across both systems.

    Usage::

        from replicate_mcp.latitude import LatitudeObservabilityBridge
        from replicate_mcp.observability import Observability

        obs = Observability()
        bridge = LatitudeObservabilityBridge(latitude_client, obs)

        # Now traces go to both Latitude and OTEL
        with bridge.trace("agent-run", "my-agent"):
            await run_agent()
    """

    def __init__(
        self,
        latitude_client: LatitudeClient,
        observability: Any,  # Observability type
    ) -> None:
        self.latitude = latitude_client
        self.observability = observability

    def trace(
        self,
        name: str,
        agent_id: str,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> _DualTraceContext:
        """Create a trace that records to both Latitude and OTEL."""
        lat_trace = self.latitude.start_trace(name, agent_id, input_data, metadata)
        otel_span = self.observability.span(name, agent_id=agent_id, **(metadata or {}))
        return _DualTraceContext(self.latitude, lat_trace, otel_span)


class _DualTraceContext:
    """Context manager that records to both Latitude and OTEL."""

    def __init__(
        self,
        client: LatitudeClient,
        lat_trace: LatitudeTrace,
        otel_span: Any,
    ) -> None:
        self.client = client
        self.lat_trace = lat_trace
        self.otel_span = otel_span

    async def __aenter__(self) -> LatitudeTrace:
        self.otel_span.__enter__()
        return self.lat_trace

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_val:
            self.lat_trace.record_error(exc_val)
            self.otel_span.record_exception(exc_val)

        await self.client.end_trace(self.lat_trace)
        self.otel_span.__exit__(exc_type, exc_val, exc_tb)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "LatitudeClient",
    "LatitudeConfig",
    "LatitudePrompt",
    "LatitudeTrace",
    "LatitudeEvalResult",
    "LatitudeError",
    "LatitudeNotConfiguredError",
    "LatitudeAPIError",
    "LatitudePaymentRequiredError",
    "LatitudePlugin",
    "LatitudeObservabilityBridge",
    "LatitudeTraceContext",
]
