"""Tests for AuditLogger, AuditRecord, and dashboard analysis helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from replicate_mcp.utils.audit import (
    AuditLogger,
    AuditRecord,
    _percentile,
    compute_cost_summary,
    filter_by_period,
)

# ---------------------------------------------------------------------------
# AuditRecord
# ---------------------------------------------------------------------------


class TestAuditRecord:
    def test_to_dict_basic(self) -> None:
        rec = AuditRecord(
            ts="2026-04-20T12:00:00+00:00",
            agent="summariser",
            model="meta/llama",
            latency_ms=3200.0,
            cost_usd=0.002,
            success=True,
            input_hash="abc123",
            session_id="s1",
        )
        d = rec.to_dict()
        assert d["agent"] == "summariser"
        assert d["latency_ms"] == 3200.0
        assert d["success"] is True
        assert "payload" not in d  # payload not included by default

    def test_to_dict_includes_payload_when_set(self) -> None:
        rec = AuditRecord(
            ts="2026-04-20T12:00:00+00:00",
            agent="a",
            model="m/m",
            latency_ms=100.0,
            cost_usd=0.0,
            success=True,
            input_hash="",
            session_id="s1",
            payload={"key": "val"},
        )
        d = rec.to_dict()
        assert d["payload"] == {"key": "val"}

    def test_from_dict_roundtrip(self) -> None:
        original = AuditRecord(
            ts="2026-04-20T12:00:00+00:00",
            agent="agent_x",
            model="owner/model",
            latency_ms=1500.0,
            cost_usd=0.003,
            success=False,
            input_hash="ff00",
            session_id="s9",
        )
        restored = AuditRecord.from_dict(original.to_dict())
        assert restored.agent == original.agent
        assert restored.model == original.model
        assert restored.success is False
        assert restored.cost_usd == pytest.approx(0.003)

    def test_from_dict_missing_keys_use_defaults(self) -> None:
        rec = AuditRecord.from_dict({})
        assert rec.agent == ""
        assert rec.success is True
        assert rec.latency_ms == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# AuditLogger — record()
# ---------------------------------------------------------------------------


class TestAuditLoggerRecord:
    def test_record_creates_file(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        log.record(agent="a", model="m/m", latency_ms=100.0, success=True)
        assert log.exists()

    def test_record_appends_valid_json_line(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        log.record(agent="a", model="m/m", latency_ms=100.0, success=True)
        lines = log.path.read_text().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["agent"] == "a"
        assert data["model"] == "m/m"

    def test_record_appends_multiple_lines(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        for i in range(5):
            log.record(agent=f"agent_{i}", model="m/m", latency_ms=float(i * 100))
        lines = log.path.read_text().splitlines()
        assert len(lines) == 5

    def test_record_computes_input_hash(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        result = log.record(
            agent="a", model="m/m", latency_ms=100.0,
            payload={"prompt": "hi"}
        )
        assert result is not None
        assert result.input_hash != ""

    def test_record_does_not_store_payload_by_default(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        log.record(agent="a", model="m/m", latency_ms=100.0,
                   payload={"secret": "value"})
        line = log.path.read_text().splitlines()[0]
        data = json.loads(line)
        assert "payload" not in data

    def test_record_stores_payload_with_log_inputs(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        log.record(agent="a", model="m/m", latency_ms=100.0,
                   payload={"prompt": "hi"}, log_inputs=True)
        line = log.path.read_text().splitlines()[0]
        data = json.loads(line)
        assert data.get("payload") == {"prompt": "hi"}

    def test_record_disabled_returns_none(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl", enabled=False)
        result = log.record(agent="a", model="m/m", latency_ms=100.0)
        assert result is None
        assert not log.exists()

    def test_record_creates_parent_dirs(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "deep" / "nested" / "audit.jsonl")
        log.record(agent="a", model="m/m", latency_ms=100.0)
        assert log.exists()


# ---------------------------------------------------------------------------
# AuditLogger — read_records()
# ---------------------------------------------------------------------------


class TestAuditLoggerRead:
    def test_read_empty_when_no_file(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "missing.jsonl")
        assert log.read_records() == []

    def test_read_all_records(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        for _ in range(10):
            log.record(agent="a", model="m/m", latency_ms=100.0)
        records = log.read_records()
        assert len(records) == 10

    def test_read_skips_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        path.write_text('{"agent":"ok","model":"m/m","latency_ms":100,"cost_usd":0,"success":true,"input_hash":"","session_id":"s"}\n{bad json\n')
        log = AuditLogger(path=path)
        records = log.read_records()
        # Only the valid line should be parsed
        assert len(records) == 1


# ---------------------------------------------------------------------------
# AuditLogger — management
# ---------------------------------------------------------------------------


class TestAuditLoggerManagement:
    def test_clear_deletes_file(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        log.record(agent="a", model="m/m", latency_ms=100.0)
        assert log.exists()
        log.clear()
        assert not log.exists()

    def test_clear_nonexistent_is_noop(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "nope.jsonl")
        log.clear()  # must not raise

    def test_size_bytes_zero_if_absent(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "nope.jsonl")
        assert log.size_bytes() == 0

    def test_size_bytes_positive_after_write(self, tmp_path: Path) -> None:
        log = AuditLogger(path=tmp_path / "audit.jsonl")
        log.record(agent="a", model="m/m", latency_ms=100.0)
        assert log.size_bytes() > 0


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_empty_list(self) -> None:
        assert _percentile([], 50) == 0.0

    def test_single_element(self) -> None:
        assert _percentile([42.0], 50) == pytest.approx(42.0)

    def test_p50_of_sorted_list(self) -> None:
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile(vals, 50) == pytest.approx(3.0)

    def test_p100_is_max(self) -> None:
        vals = sorted([10.0, 20.0, 30.0])
        assert _percentile(vals, 100) == pytest.approx(30.0)


class TestComputeCostSummary:
    def _make_record(
        self, agent: str, model: str, cost: float, latency: float, success: bool
    ) -> AuditRecord:
        return AuditRecord(
            ts="2026-04-20T12:00:00+00:00",
            agent=agent,
            model=model,
            latency_ms=latency,
            cost_usd=cost,
            success=success,
            input_hash="",
            session_id="s",
        )

    def test_aggregates_by_model(self) -> None:
        records = [
            self._make_record("a", "m/llama", 0.001, 3000, True),
            self._make_record("a", "m/llama", 0.002, 2000, True),
            self._make_record("b", "m/flux", 0.005, 5000, False),
        ]
        summary = compute_cost_summary(records)
        assert summary["m/llama"]["calls"] == 2
        assert summary["m/llama"]["successes"] == 2
        assert summary["m/llama"]["cost_usd"] == pytest.approx(0.003)
        assert summary["m/flux"]["calls"] == 1
        assert summary["m/flux"]["successes"] == 0

    def test_empty_records(self) -> None:
        assert compute_cost_summary([]) == {}


class TestFilterByPeriod:
    def _record(self, ts: str) -> AuditRecord:
        return AuditRecord(
            ts=ts, agent="a", model="m/m", latency_ms=100.0,
            cost_usd=0.0, success=True, input_hash="", session_id="s"
        )

    def test_all_period_returns_everything(self) -> None:
        records = [
            self._record("2020-01-01T00:00:00+00:00"),
            self._record("2026-04-20T12:00:00+00:00"),
        ]
        assert len(filter_by_period(records, "all")) == 2

    def test_today_filters_old_records(self) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        now_ts = datetime.now(timezone.utc).isoformat()
        records = [self._record(old_ts), self._record(now_ts)]
        result = filter_by_period(records, "today")
        assert len(result) == 1

    def test_week_includes_recent(self) -> None:
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        records = [self._record(recent), self._record(old)]
        result = filter_by_period(records, "week")
        assert len(result) == 1

    def test_month_includes_recent(self) -> None:
        recent = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        records = [self._record(recent), self._record(old)]
        result = filter_by_period(records, "month")
        assert len(result) == 1
