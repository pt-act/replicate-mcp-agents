"""Local invocation audit log and cost dashboard for Replicate MCP agents.

Every agent invocation produces an audit record written atomically to an
append-only JSONL file (one JSON object per line).  The file is human-readable,
``grep``-able, and requires zero external infrastructure.

Records contain:

- ``ts``         — ISO-8601 UTC timestamp of the invocation.
- ``agent``      — The ``safe_name`` of the invoked agent.
- ``model``      — Full Replicate model identifier (``owner/name``).
- ``latency_ms`` — Wall-clock duration of the invocation in milliseconds.
- ``cost_usd``   — Reported cost in USD (0.0 if not known).
- ``success``    — ``true`` if the invocation succeeded, ``false`` otherwise.
- ``input_hash`` — SHA-256 of the serialised input payload (privacy-safe by
                   default; the actual input is stored only when
                   ``log_inputs=True`` is passed to :meth:`AuditLogger.record`).
- ``session_id`` — Unique identifier for the current process session.

Usage::

    from replicate_mcp.utils.audit import AuditLogger

    logger = AuditLogger()                  # default: ~/.replicate/audit.jsonl
    await logger.record(
        agent="summariser",
        model="meta/llama-3-70b-instruct",
        latency_ms=3241.0,
        cost_usd=0.0021,
        success=True,
        payload={"prompt": "Summarise this…"},
    )

CLI access::

    replicate-agent audit tail              # last 20 invocations
    replicate-agent audit costs --today     # spend breakdown today
    replicate-agent audit stats             # p50/p95/p99 latency + success rate
    replicate-agent audit clear             # delete the audit file
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default path
# ---------------------------------------------------------------------------

_DEFAULT_AUDIT_PATH = Path.home() / ".replicate" / "audit.jsonl"

# Module-level session ID — constant for the lifetime of the process.
_SESSION_ID: str = str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# AuditRecord — in-memory representation of a single invocation
# ---------------------------------------------------------------------------


@dataclass
class AuditRecord:
    """A single audit record for one agent invocation.

    Attributes:
        ts:          ISO-8601 UTC timestamp.
        agent:       Agent safe_name.
        model:       Full Replicate model path.
        latency_ms:  Wall-clock latency in milliseconds.
        cost_usd:    Cost reported by Replicate (0.0 if unknown).
        success:     Whether the invocation succeeded.
        input_hash:  SHA-256 of the serialised payload (or empty string).
        session_id:  Short identifier for the current process session.
        payload:     Actual input payload — only populated when log_inputs=True.
    """

    ts: str
    agent: str
    model: str
    latency_ms: float
    cost_usd: float
    success: bool
    input_hash: str
    session_id: str
    payload: dict[str, Any] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict suitable for JSONL output."""
        d: dict[str, Any] = {
            "ts": self.ts,
            "agent": self.agent,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "cost_usd": self.cost_usd,
            "success": self.success,
            "input_hash": self.input_hash,
            "session_id": self.session_id,
        }
        if self.payload is not None:
            d["payload"] = self.payload
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditRecord:
        """Deserialise from a raw JSONL dict."""
        return cls(
            ts=data.get("ts", ""),
            agent=data.get("agent", ""),
            model=data.get("model", ""),
            latency_ms=float(data.get("latency_ms", 0.0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            success=bool(data.get("success", True)),
            input_hash=data.get("input_hash", ""),
            session_id=data.get("session_id", ""),
            payload=data.get("payload"),
        )


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


@dataclass
class AuditLogger:
    """Append-only JSONL audit log for Replicate agent invocations.

    Args:
        path:    Path to the JSONL log file.  Defaults to
                 ``~/.replicate/audit.jsonl``.  Parent directories are
                 created automatically.
        enabled: Set to ``False`` to make all :meth:`record` calls no-ops
                 (useful in tests or when audit logging is explicitly opted out).

    Example::

        audit = AuditLogger()
        await audit.record(
            agent="flux_pro",
            model="black-forest-labs/flux-1.1-pro",
            latency_ms=4200.0,
            cost_usd=0.003,
            success=True,
            payload={"prompt": "a red panda"},
            log_inputs=True,
        )
    """

    path: Path = field(default_factory=lambda: _DEFAULT_AUDIT_PATH)
    enabled: bool = True

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # ---- write ----

    def record(
        self,
        *,
        agent: str,
        model: str,
        latency_ms: float,
        cost_usd: float = 0.0,
        success: bool = True,
        payload: dict[str, Any] | None = None,
        log_inputs: bool = False,
    ) -> AuditRecord | None:
        """Append one audit record to the JSONL log.

        Writes are append-only and use a single ``write()`` call which is
        atomic on POSIX systems for writes smaller than ``PIPE_BUF`` (≥ 4 KiB).
        For very large payloads, consider ``log_inputs=False`` (the default).

        Args:
            agent:       Agent safe_name.
            model:       Full Replicate model identifier.
            latency_ms:  Wall-clock duration in milliseconds.
            cost_usd:    Reported cost in USD (0.0 if unknown).
            success:     Whether the invocation succeeded.
            payload:     Raw input payload dict.  Only included in the record
                         when *log_inputs* is ``True``.
            log_inputs:  If ``True``, store the full payload in the record.
                         Defaults to ``False`` (privacy-safe).

        Returns:
            The :class:`AuditRecord` that was written, or ``None`` if audit
            logging is disabled.
        """
        if not self.enabled:
            return None

        input_hash = ""
        if payload is not None:
            try:
                serialised = json.dumps(payload, sort_keys=True, default=str)
                input_hash = hashlib.sha256(serialised.encode()).hexdigest()[:16]
            except (TypeError, ValueError):
                input_hash = "unhashable"

        record = AuditRecord(
            ts=datetime.now(timezone.utc).isoformat(),
            agent=agent,
            model=model,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            success=success,
            input_hash=input_hash,
            session_id=_SESSION_ID,
            payload=payload if log_inputs else None,
        )

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.to_dict(), default=str) + "\n"
            with open(self.path, "a", encoding="utf-8") as fh:  # noqa: PTH123
                fh.write(line)
        except OSError as exc:
            logger.warning("Audit log write failed: %s", exc)

        return record

    # ---- read ----

    def read_records(self) -> list[AuditRecord]:
        """Read and parse all records from the audit log.

        Returns:
            List of :class:`AuditRecord` objects in chronological order.
            Returns an empty list if the file does not exist or is unreadable.
        """
        if not self.path.exists():
            return []
        records: list[AuditRecord] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(AuditRecord.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    logger.debug("Skipping malformed audit record: %r", line[:80])
        except OSError as exc:
            logger.warning("Could not read audit log %s: %s", self.path, exc)
        return records

    # ---- management ----

    def clear(self) -> None:
        """Delete the audit log file.  A no-op if it does not exist."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def exists(self) -> bool:
        """Return ``True`` if the audit log file exists."""
        return self.path.exists()

    def size_bytes(self) -> int:
        """Return the size of the audit log file in bytes (0 if absent)."""
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


# ---------------------------------------------------------------------------
# Analysis helpers used by the CLI dashboard commands
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Return the *pct*-th percentile of a pre-sorted list."""
    if not sorted_values:
        return 0.0
    idx = (len(sorted_values) - 1) * pct / 100.0
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def compute_cost_summary(
    records: list[AuditRecord],
) -> dict[str, dict[str, Any]]:
    """Aggregate cost and call statistics per model.

    Returns:
        Dict mapping model → ``{calls, successes, cost_usd, latencies}``.
    """
    summary: dict[str, dict[str, Any]] = {}
    for rec in records:
        entry = summary.setdefault(
            rec.model,
            {"calls": 0, "successes": 0, "cost_usd": 0.0, "latencies": []},
        )
        entry["calls"] += 1
        if rec.success:
            entry["successes"] += 1
        entry["cost_usd"] += rec.cost_usd
        entry["latencies"].append(rec.latency_ms)
    return summary


def filter_by_period(
    records: list[AuditRecord],
    period: str,
) -> list[AuditRecord]:
    """Filter *records* to those within *period* (``today``, ``week``, ``month``, ``all``).

    Comparison is done against the UTC timestamp of each record.
    """
    if period == "all":
        return records

    now = datetime.now(timezone.utc)

    def _cutoff() -> datetime:
        if period == "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "week":
            import datetime as _dt  # noqa: PLC0415

            return now - _dt.timedelta(days=7)
        if period == "month":
            import datetime as _dt  # noqa: PLC0415

            return now - _dt.timedelta(days=30)
        return datetime.min.replace(tzinfo=timezone.utc)

    cutoff = _cutoff()
    filtered = []
    for rec in records:
        try:
            rec_time = datetime.fromisoformat(rec.ts)
            if rec_time.tzinfo is None:
                rec_time = rec_time.replace(tzinfo=timezone.utc)
            if rec_time >= cutoff:
                filtered.append(rec)
        except (ValueError, TypeError):
            pass
    return filtered


__all__ = [
    "AuditLogger",
    "AuditRecord",
    "compute_cost_summary",
    "filter_by_period",
]
