"""Telemetry and cost tracking utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TelemetryEvent:
    """Represents a single agent invocation measurement."""

    agent_id: str
    cost_usd: float
    latency_ms: float
    timestamp: datetime = datetime.utcnow()


class TelemetryTracker:
    """In-memory accumulator for telemetry events."""

    def __init__(self) -> None:
        self._events: list[TelemetryEvent] = []

    def record(self, event: TelemetryEvent) -> None:
        self._events.append(event)

    def total_cost(self) -> float:
        return sum(event.cost_usd for event in self._events)

    def average_latency(self) -> float:
        if not self._events:
            return 0.0
        return sum(event.latency_ms for event in self._events) / len(self._events)

    @property
    def events(self) -> list[TelemetryEvent]:
        return list(self._events)


__all__ = ["TelemetryEvent", "TelemetryTracker"]
