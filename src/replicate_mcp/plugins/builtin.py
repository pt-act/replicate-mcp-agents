"""Built-in guardrail plugins shipped with replicate-mcp-agents.

Phase 6 — Differentiation.  Provides three guardrail plugins:

* :class:`PIIMaskPlugin`       — redacts personally identifiable information.
* :class:`ContentFilterPlugin` — blocks prompts / outputs matching deny-lists.
* :class:`CostCapPlugin`       — enforces per-invocation and session cost limits.

All three extend :class:`~replicate_mcp.plugins.base.BasePlugin` and can be
loaded via :func:`~replicate_mcp.plugins.loader.load_plugins` with the
*extra_classes* parameter, or registered as entry points in third-party
packages.
"""

from __future__ import annotations

import re
from typing import Any

from replicate_mcp.plugins.base import BasePlugin, PluginMetadata

# ---------------------------------------------------------------------------
# PII Masking
# ---------------------------------------------------------------------------

_PII_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{3}[-.]?\d{2}[-.]?\d{4}\b", "[SSN]"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[EMAIL]"),
    (r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[PHONE]"),
    (r"\b\d{16,19}\b", "[CREDIT_CARD]"),
]


class PIIMaskPlugin(BasePlugin):
    """Redact common PII patterns from agent payloads and results.

    Scans for Social Security numbers, email addresses, phone numbers,
    and credit-card-like digit sequences, replacing them with placeholder
    tokens.
    """

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="pii_mask",
            version="1.0.0",
            description="Redacts PII (SSN, email, phone, credit card) from payloads.",
        )

    def setup(self) -> None:  # noqa: PLR6301
        """No external resources needed."""

    def teardown(self) -> None:  # noqa: PLR6301
        """No external resources to release."""

    def on_agent_run(
        self, agent_name: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        return self._mask_payload(payload)

    def on_agent_result(
        self, agent_name: str, chunks: list[dict[str, Any]], latency_ms: float
    ) -> list[dict[str, Any]] | None:
        masked = [self._mask_payload(c) for c in chunks]  # type: ignore[arg-type]
        return masked if any(masked) else None

    # ---- private ----

    @staticmethod
    def _mask_text(text: str) -> str:
        for pattern, replacement in _PII_PATTERNS:
            text = re.sub(pattern, replacement, text)
        return text

    @classmethod
    def _mask_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        masked: dict[str, Any] = {}
        changed = False
        for key, value in payload.items():
            if isinstance(value, str):
                new_value = cls._mask_text(value)
                if new_value != value:
                    changed = True
                masked[key] = new_value
            else:
                masked[key] = value
        return masked if changed else payload


# ---------------------------------------------------------------------------
# Content Filter
# ---------------------------------------------------------------------------

_DEFAULT_DENY_LIST: list[str] = []


class ContentFilterPlugin(BasePlugin):
    """Block prompts and outputs that contain deny-listed terms.

    This is a **simple keyword filter** — it performs exact (case-insensitive)
    matching against a configurable deny list.  If any term matches, the
    entire payload is rejected by replacing it with a safe placeholder.
    """

    def __init__(self, deny_list: list[str] | None = None) -> None:
        self._deny_list = deny_list or _DEFAULT_DENY_LIST

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="content_filter",
            version="1.0.0",
            description="Filters prompts and outputs against a deny list.",
        )

    def setup(self) -> None:  # noqa: PLR6301
        """No external resources needed."""

    def teardown(self) -> None:  # noqa: PLR6301
        """No external resources to release."""

    def on_agent_run(
        self, agent_name: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self._matches(payload):
            return {**payload, "prompt": "[CONTENT BLOCKED]"}
        return None

    def on_agent_result(
        self, agent_name: str, chunks: list[dict[str, Any]], latency_ms: float
    ) -> list[dict[str, Any]] | None:
        if any(self._matches(c) for c in chunks):  # type: ignore[arg-type]
            return [{**c, "text": "[CONTENT BLOCKED]"} for c in chunks]  # type: ignore[arg-type]
        return None

    # ---- private ----

    def _matches(self, payload: dict[str, Any]) -> bool:
        text = " ".join(str(v) for v in payload.values()).lower()
        return any(term.lower() in text for term in self._deny_list)


# ---------------------------------------------------------------------------
# Cost Cap
# ---------------------------------------------------------------------------


class CostCapPlugin(BasePlugin):
    """Enforce per-invocation and cumulative session cost limits.

    When a cost estimate exceeds the configured cap, the invocation is
    blocked by returning a safe placeholder payload via :meth:`on_agent_run`.

    Attributes:
        per_invocation_cap: Maximum estimated USD per single invocation.
        session_cap:        Maximum cumulative USD across all invocations
                           in the session.
    """

    def __init__(
        self,
        per_invocation_cap: float = 1.0,
        session_cap: float = 10.0,
    ) -> None:
        self.per_invocation_cap = per_invocation_cap
        self.session_cap = session_cap
        self._session_spend: float = 0.0

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="cost_cap",
            version="1.0.0",
            description="Enforces per-invocation and session cost limits.",
        )

    def setup(self) -> None:  # noqa: PLR6301
        self._session_spend = 0.0

    def teardown(self) -> None:  # noqa: PLR6301
        self._session_spend = 0.0

    def on_agent_run(
        self, agent_name: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        estimated = float(payload.get("estimated_cost_usd", 0.0))
        if estimated > self.per_invocation_cap:
            return {**payload, "prompt": "[COST CAP EXCEEDED — INVOCATION BLOCKED]"}
        if self._session_spend + estimated > self.session_cap:
            return {**payload, "prompt": "[COST CAP EXCEEDED — SESSION LIMIT]"}
        return None

    def on_agent_result(
        self, agent_name: str, chunks: list[dict[str, Any]], latency_ms: float
    ) -> list[dict[str, Any]] | None:
        # Accumulate actual cost from result metadata if present
        for chunk in chunks:
            cost = float(chunk.get("cost_usd", 0.0))  # type: ignore[arg-type]
            self._session_spend += cost
        return None

    @property
    def session_spend(self) -> float:
        """Total spend accumulated in the current session."""
        return self._session_spend


__all__ = [
    "PIIMaskPlugin",
    "ContentFilterPlugin",
    "CostCapPlugin",
]
