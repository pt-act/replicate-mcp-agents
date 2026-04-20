"""Unit tests for the telemetry module."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from replicate_mcp.utils.telemetry import TelemetryEvent, TelemetryTracker


class TestTelemetryEvent:
    """Tests for TelemetryEvent dataclass."""

    def test_basic_construction(self) -> None:
        event = TelemetryEvent(agent_id="llama", cost_usd=0.01, latency_ms=150.0)
        assert event.agent_id == "llama"
        assert event.cost_usd == 0.01
        assert event.latency_ms == 150.0

    def test_timestamp_is_timezone_aware(self) -> None:
        event = TelemetryEvent(agent_id="a", cost_usd=0.0, latency_ms=0.0)
        assert event.timestamp.tzinfo is not None
        assert event.timestamp.tzinfo == timezone.utc

    def test_each_event_gets_unique_timestamp(self) -> None:
        """Regression: ensure the mutable default bug is fixed."""
        e1 = TelemetryEvent(agent_id="a", cost_usd=0.0, latency_ms=0.0)
        time.sleep(0.01)
        e2 = TelemetryEvent(agent_id="b", cost_usd=0.0, latency_ms=0.0)
        assert e1.timestamp != e2.timestamp
        assert e2.timestamp > e1.timestamp

    def test_custom_timestamp(self) -> None:
        ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
        event = TelemetryEvent(agent_id="a", cost_usd=1.0, latency_ms=50.0, timestamp=ts)
        assert event.timestamp == ts

    def test_zero_cost_and_latency(self) -> None:
        event = TelemetryEvent(agent_id="free", cost_usd=0.0, latency_ms=0.0)
        assert event.cost_usd == 0.0
        assert event.latency_ms == 0.0


class TestTelemetryTracker:
    """Tests for TelemetryTracker accumulator."""

    def test_empty_tracker(self) -> None:
        tracker = TelemetryTracker()
        assert tracker.total_cost() == 0.0
        assert tracker.average_latency() == 0.0
        assert tracker.events == []

    def test_record_single_event(self) -> None:
        tracker = TelemetryTracker()
        event = TelemetryEvent(agent_id="a", cost_usd=0.05, latency_ms=200.0)
        tracker.record(event)
        assert tracker.total_cost() == 0.05
        assert tracker.average_latency() == 200.0
        assert len(tracker.events) == 1

    def test_record_multiple_events(self) -> None:
        tracker = TelemetryTracker()
        tracker.record(TelemetryEvent(agent_id="a", cost_usd=0.10, latency_ms=100.0))
        tracker.record(TelemetryEvent(agent_id="b", cost_usd=0.20, latency_ms=300.0))
        tracker.record(TelemetryEvent(agent_id="c", cost_usd=0.05, latency_ms=200.0))
        assert abs(tracker.total_cost() - 0.35) < 1e-9
        assert tracker.average_latency() == 200.0
        assert len(tracker.events) == 3

    def test_events_returns_copy(self) -> None:
        """Ensure .events returns a copy, not the internal list."""
        tracker = TelemetryTracker()
        tracker.record(TelemetryEvent(agent_id="a", cost_usd=0.01, latency_ms=10.0))
        events = tracker.events
        events.clear()  # mutate the copy
        assert len(tracker.events) == 1  # internal list unchanged

    def test_total_cost_precision(self) -> None:
        tracker = TelemetryTracker()
        for _ in range(100):
            tracker.record(TelemetryEvent(agent_id="a", cost_usd=0.01, latency_ms=10.0))
        assert abs(tracker.total_cost() - 1.0) < 1e-9