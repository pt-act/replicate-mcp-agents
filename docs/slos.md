# Service Level Objectives (SLOs) · v1.0

Replicate MCP Agents — Sprint S8 · 2026-04-20

---

## Overview

These SLOs define the reliability and performance targets for the
`replicate-mcp-agents` system across all interaction surfaces:
CLI, MCP server, and direct Python API.

**Grade targets:**

| Grade | Availability | P95 Overhead | Error Rate | MTTR |
|-------|-------------|--------------|------------|------|
| **A** | ≥ 99.5 %    | < 200 ms     | < 1 %      | < 4 hr |
| **A+** | ≥ 99.9 %  | < 100 ms     | < 0.1 %    | < 1 hr |

---

## SLO Definitions

### SLO-1 · Availability

| Item | Value |
|------|-------|
| **Objective** | The MCP server successfully handles ≥ 99.5 % of tool-call requests within a rolling 30-day window. |
| **A grade target** | 99.5 % (≤ 3.65 hr downtime/month) |
| **A+ grade target** | 99.9 % (≤ 43.8 min downtime/month) |
| **Measurement** | Uptime monitor pings `initialize` every 60 s; failure if no response within 5 s. |
| **Exclusions** | Planned maintenance windows (≤ 4 hr/month) with 48 hr notice. |
| **Error budget** | 0.5 % ≈ 3.65 hr/month (A); 0.1 % ≈ 43.8 min/month (A+). |

### SLO-2 · P95 Overhead Latency

| Item | Value |
|------|-------|
| **Objective** | 95th-percentile *overhead* (i.e. time spent in this library, excluding Replicate API wall-clock time) is < 200 ms. |
| **A grade target** | P95 < 200 ms |
| **A+ grade target** | P95 < 100 ms |
| **Measurement** | `replicate_mcp.invocation.latency` histogram in OTEL; exclude the Replicate API latency span. |
| **Benchmark** | `tests/load/locustfile.py` nightly CI job at 50 concurrent users, 60 s duration. |

### SLO-3 · Error Rate

| Item | Value |
|------|-------|
| **Objective** | Fewer than 1 % of agent invocations result in an unhandled error (500-class, timeout, or unrecovered circuit-open). |
| **A grade target** | < 1 % |
| **A+ grade target** | < 0.1 % |
| **Measurement** | `replicate_mcp.error.count / replicate_mcp.invocation.count` (rolling 5 min window). |
| **Notes** | Replicate API 4xx (bad input from user) are not counted against this SLO. |

### SLO-4 · Cost Tracking Accuracy

| Item | Value |
|------|-------|
| **Objective** | Reported cost vs Replicate invoice within ± 10 %. |
| **A grade target** | ± 10 % |
| **A+ grade target** | ± 5 % |
| **Measurement** | Monthly reconciliation of `TelemetryTracker.total_cost()` against Replicate billing API. |

### SLO-5 · MTTR (Mean Time to Restore)

| Item | Value |
|------|-------|
| **Objective** | After a production incident is detected, the system is restored within 4 hours. |
| **A grade target** | < 4 hr |
| **A+ grade target** | < 1 hr |
| **Measurement** | PagerDuty incident duration from `TRIGGERED` to `RESOLVED`. |

### SLO-6 · Circuit Breaker Recovery

| Item | Value |
|------|-------|
| **Objective** | After the Replicate API recovers from an outage, the circuit breaker transitions from OPEN → CLOSED within `recovery_timeout` + `half_open_max_calls × avg_latency`. |
| **A grade target** | ≤ 90 s (with default `recovery_timeout=60s`, `half_open_max_calls=3`, `avg_latency=10s`). |
| **Measurement** | `replicate_mcp.circuit_breaker.trips` counter and span events in OTEL. |

---

## Error Budget Policy

| Burn Rate | Action |
|-----------|--------|
| > 2× budget consumed in 1 hr | Page on-call immediately |
| > 1× budget consumed in 6 hr | Page on-call; begin incident investigation |
| > 0.5× budget consumed in 24 hr | Slack alert; schedule postmortem if trend continues |
| On budget | No action; weekly review |

---

## Monitoring Stack

```
Traces:   OpenTelemetry SDK → OTLP → Jaeger / Honeycomb
Metrics:  OTEL counters/histograms → Prometheus → Grafana
Logs:     structlog (JSON) → stdout → Loki / CloudWatch
Alerting: Grafana alerts → PagerDuty → on-call rotation
```

### Key Grafana Dashboards

| Dashboard | Panels |
|-----------|--------|
| `Invocation Overview` | Total rate, error rate, P50/P95/P99 latency |
| `Cost Tracking` | Cost/hour by model, cumulative spend, EMA vs actual |
| `Circuit Breakers` | State per model, trip count, recovery time |
| `Router Leaderboard` | EMA cost/latency/quality per model, Thompson samples |
| `Error Analysis` | Error rate by type, top error messages |

---

## Review Cadence

- **Weekly:** SLO burn rate reviewed by SRE.
- **Monthly:** Full SLO review; update targets if capacity changes.
- **Per release:** Load tests re-run; SLO baselines re-established.
- **Post-incident:** Blameless postmortem within 48 hr; SLO impact documented.

---

*Version 1.0 · Approved by Eng Lead · 2026-04-20*