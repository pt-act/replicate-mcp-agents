"""Secret management and security utilities.

Sprint S5 — Hardening.  Covers:

* ``SecretManager``    — retrieve API tokens from env / keyring; never logs them.
* ``SecretMasker``     — redact secret-looking values from dicts before logging.
* ``sanitize_otel``    — strip sensitive OTEL span attributes.
* ``validate_token``   — lightweight format check for Replicate tokens.

Design decisions (see ADR-003):
    - Secrets are *never* persisted to disk or emitted to logs.
    - The ``SecretManager`` tries :mod:`keyring` first, falls back to
      environment variables, and raises :class:`SecretNotFoundError` if
      neither source has the key.
    - Pattern-based masking catches common token shapes even if they
      appear inside nested dicts or lists.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, cast

from replicate_mcp.exceptions import ReplicateMCPError

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SecretNotFoundError(ReplicateMCPError):
    """Raised when a required secret cannot be found in any source."""

    def __init__(self, key: str, sources: list[str] | None = None) -> None:
        sources_msg = ", ".join(sources or ["environment", "keyring"])
        super().__init__(f"Secret '{key}' not found in [{sources_msg}]")
        self.key = key


class InsecureConfigError(ReplicateMCPError):
    """Raised when a configuration violates a security policy."""


# ---------------------------------------------------------------------------
# Token patterns — common API key shapes to redact from logs
# ---------------------------------------------------------------------------

_REDACT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\br[0-9A-Za-z]{38}\b"),          # Replicate tokens
    re.compile(r"\bsk-[0-9A-Za-z]{40,60}\b"),      # OpenAI-style tokens
    re.compile(r"\bbearer\s+[0-9A-Za-z\-_.]{20,}\b", re.IGNORECASE),
    re.compile(r"\btoken[=:\s]+[0-9A-Za-z\-_.]{16,}\b", re.IGNORECASE),
]

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "api_token",
        "auth",
        "authorization",
        "password",
        "replicate_api_token",
        "secret",
        "token",
    }
)


# ---------------------------------------------------------------------------
# Secret masker
# ---------------------------------------------------------------------------


class SecretMasker:
    """Redact secrets from strings and dicts before they reach logs.

    The masker works on two levels:

    1. **Key-based**: any dict key whose name (case-insensitive, stripped
       of ``-`` / ``_``) matches a known sensitive word is replaced with
       ``"***"``.
    2. **Pattern-based**: any string value that *looks like* a known
       token format is replaced with ``"<redacted>"``.
    """

    def __init__(
        self,
        extra_patterns: list[re.Pattern[str]] | None = None,
        extra_keys: set[str] | None = None,
    ) -> None:
        self._patterns = list(_REDACT_PATTERNS) + (extra_patterns or [])
        self._keys = _SENSITIVE_KEYS | {k.lower() for k in (extra_keys or set())}

    # ---- public API ----

    def mask_string(self, value: str) -> str:
        """Return *value* with secret-looking substrings replaced by ``<redacted>``."""
        for pattern in self._patterns:
            value = pattern.sub("<redacted>", value)
        return value

    def mask_value(self, value: str) -> str:
        """Abbreviate a known-secret *value* like ``abcd...wxyz``."""
        if len(value) <= 8:
            return "***"
        return f"{value[:4]}...{value[-4:]}"

    def sanitize(self, data: Any) -> Any:  # noqa: ANN401
        """Recursively sanitise *data* (dict / list / str / scalar).

        Returns a new object; the original is not mutated.
        """
        if isinstance(data, dict):
            return {k: self._sanitize_entry(k, v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.sanitize(item) for item in data]
        if isinstance(data, str):
            return self.mask_string(data)
        return data

    # ---- private ----

    def _sanitize_entry(self, key: str, value: Any) -> Any:  # noqa: ANN401
        normalised_key = re.sub(r"[-_]", "", key.lower())
        if normalised_key in {re.sub(r"[-_]", "", k) for k in self._keys}:
            if isinstance(value, str):
                return self.mask_value(value)
            return "***"
        return self.sanitize(value)


# ---------------------------------------------------------------------------
# OTEL attribute sanitiser
# ---------------------------------------------------------------------------


def sanitize_otel_attributes(
    attributes: dict[str, Any],
    masker: SecretMasker | None = None,
) -> dict[str, Any]:
    """Remove or redact sensitive values from an OTEL span attribute dict.

    Strips any attribute whose key contains a sensitive keyword and
    applies pattern-based masking to remaining string values.

    Args:
        attributes: Raw OTEL span attributes.
        masker: Optional :class:`SecretMasker` instance; a default one
                is created if not supplied.

    Returns:
        A sanitised copy of *attributes*.
    """
    masker = masker or SecretMasker()
    return cast(dict[str, Any], masker.sanitize(attributes))


# ---------------------------------------------------------------------------
# Secret manager
# ---------------------------------------------------------------------------


@dataclass
class SecretManager:
    """Retrieve secrets from environment variables or the system keyring.

    Resolution order:
        1. Environment variable ``{key}`` (always tried first).
        2. System keyring via :mod:`keyring` (if installed and the key
           exists in the default keyring service).

    The token is *never* stored on the instance — it is fetched fresh
    on every call to :meth:`get_token` so that rotated secrets are
    picked up without restart.
    """

    keyring_service: str = "replicate-mcp-agents"
    _masker: SecretMasker = field(
        default_factory=SecretMasker, init=False, repr=False
    )

    def get_token(
        self,
        env_var: str = "REPLICATE_API_TOKEN",
        *,
        required: bool = True,
    ) -> str:
        """Return the secret for *env_var*, raising if it cannot be found.

        Args:
            env_var:  Environment variable name to look up.
            required: If ``True`` (default) raise
                      :class:`SecretNotFoundError` when the secret is
                      absent from all sources.  If ``False`` return ``""``.
        """
        # 1. Environment
        value = os.environ.get(env_var, "").strip()
        if value:
            return value

        # 2. System keyring (optional dep)
        try:
            import keyring  # type: ignore[import-untyped,import-not-found]

            kr_value = keyring.get_password(self.keyring_service, env_var)
            if kr_value:
                return str(kr_value).strip()
        except Exception:  # noqa: BLE001, S110
            pass  # keyring not installed or not configured

        if required:
            raise SecretNotFoundError(env_var, sources=["environment", "keyring"])
        return ""

    def masked_token(self, env_var: str = "REPLICATE_API_TOKEN") -> str:
        """Return a masked representation of the token (safe for logging)."""
        try:
            token = self.get_token(env_var, required=True)
            return self._masker.mask_value(token)
        except SecretNotFoundError:
            return "<not set>"

    def validate_replicate_token(self, token: str) -> bool:
        """Return ``True`` if *token* passes a basic format check.

        Replicate API tokens start with ``r`` followed by 38 alphanumeric
        characters (39 chars total), though the exact format can vary.
        This is a heuristic, not a cryptographic verification.
        """
        return bool(re.match(r"^r[0-9A-Za-z]{38}$", token))


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------


def assert_no_eval_in_config(config_dict: dict[str, Any]) -> None:
    """Raise :class:`InsecureConfigError` if *config_dict* contains eval-able strings.

    Scans all string values recursively for ``eval(``, ``exec(``, or
    ``__import__`` patterns — residuals that may appear if YAML configs
    were built with old tooling.
    """
    _forbidden_pat = re.compile(r"\b(eval|exec|__import__|compile)\s*\(")

    def _check(obj: Any, path: str) -> None:  # noqa: ANN401
        if isinstance(obj, str) and _forbidden_pat.search(obj):
            raise InsecureConfigError(
                f"Forbidden eval/exec pattern detected at '{path}': {obj[:80]!r}"
            )
        if isinstance(obj, dict):
            for k, v in obj.items():
                _check(v, f"{path}.{k}")
        if isinstance(obj, list):
            for i, item in enumerate(obj):
                _check(item, f"{path}[{i}]")

    _check(config_dict, "root")


__all__ = [
    "SecretNotFoundError",
    "InsecureConfigError",
    "SecretMasker",
    "SecretManager",
    "sanitize_otel_attributes",
    "assert_no_eval_in_config",
]
