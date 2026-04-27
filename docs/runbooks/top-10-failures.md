# Operations Runbook: Top 10 Failure Modes

Replicate MCP Agents · Sprint S8 · 2026-04-20

---

## How to Use This Runbook

1. Find the failure signature in the table below.
2. Follow the **Immediate Actions** to stabilise.
3. Follow the **Root Cause Steps** to diagnose.
4. Apply the **Remediation** to fix.
5. File a postmortem if the incident lasted > 1 hour or impacted users.

---

## Failure 1 · REPLICATE_API_TOKEN Not Set

**Symptoms:**
- Every agent invocation returns `{"error": "REPLICATE_API_TOKEN is not set"}`.
- CLI `agents run` exits 1 with red error message.

**Immediate Actions:**
```bash
export REPLICATE_API_TOKEN=<your-token>
replicate-agent status  # verify token is now detected
```

**Root Cause Steps:**
1. Check the process environment: `env | grep REPLICATE`.
2. Check `.env` file or secret manager configuration.
3. Verify the token is valid: `curl -H "Authorization: Token $REPLICATE_API_TOKEN" https://api.replicate.com/v1/account`.

**Remediation:**
- Set the token in the runtime environment (systemd unit, Docker `--env-file`, Kubernetes Secret).
- Add `SecretManager.validate_replicate_token()` check to startup probes.

---

## Failure 2 · Circuit Breaker Stuck OPEN

**Symptoms:**
- All invocations for a specific model return `{"error": "Circuit open for '...' — service unavailable"}`.
- `replicate_mcp.circuit_breaker.trips` counter is elevated.

**Immediate Actions:**
```python
# Reset the breaker for a specific model
from replicate_mcp.server import _executor
breaker = _executor.circuit_breaker("meta/meta-llama-3-70b-instruct")
breaker.reset()
```

**Root Cause Steps:**
1. Check Replicate status page: https://replicate.com/status
2. Check circuit breaker state in OTEL dashboard → "Circuit Breakers" panel.
3. Verify `recovery_timeout` (default 60s) has elapsed.

**Remediation:**
- If Replicate is healthy, the circuit will auto-recover after `recovery_timeout`.
- If Replicate is degraded, route traffic to an alternative model via the router:
  ```python
  from replicate_mcp.server import _router
  _router.record_outcome("meta/meta-llama-3-70b-instruct",
                         latency_ms=60000, cost_usd=0, success=False)
  ```

---

## Failure 3 · Replicate API Rate Limiting (429)

**Symptoms:**
- Logs show `429 Too Many Requests` from Replicate.
- High retry count in OTEL traces.
- `replicate_mcp.error.count` elevated.

**Immediate Actions:**
1. Reduce `max_concurrency` on `AgentExecutor`:
   ```python
   executor = AgentExecutor(max_concurrency=3)
   ```
2. Enable rate limiter if not already active:
   ```python
   from replicate_mcp.ratelimit import TokenBucket
   bucket = TokenBucket(rate=5.0, capacity=10.0)  # 5 req/s burst 10
   executor = AgentExecutor(rate_limiter=bucket)
   ```

**Root Cause Steps:**
1. Check concurrency settings in `AgentExecutor`.
2. Review OTEL latency histogram for request bunching.
3. Contact Replicate support to negotiate higher rate limits.

**Remediation:**
- Set `rate_limiter` on `AgentExecutor` in production configuration.
- Implement a per-model `TokenBucket` via `RateLimiter` registry.

---

## Failure 4 · Checkpoint Corruption / Partial Write

**Symptoms:**
- `FileNotFoundError: Checkpoint <id> not found` despite file existing.
- `json.JSONDecodeError` when loading a checkpoint.

**Immediate Actions:**
1. List checkpoints: `CheckpointManager.list_sessions()`.
2. Inspect the corrupted file: `cat <checkpoint_dir>/<session_id>.json`.
3. Delete the corrupted checkpoint and restart the workflow from scratch:
   ```python
   ckpt.delete(session_id)
   ```

**Root Cause Steps:**
1. Check for `.tmp` files left in the checkpoint directory (incomplete atomic writes).
2. Check disk space: `df -h`.
3. Check for kill signals during writes: `dmesg | grep oom`.

**Remediation:**
- `CheckpointManager` uses `os.replace()` (POSIX-atomic). Corruption requires an OS-level issue.
- Add disk-space monitoring alert (alert at 80% full).
- Clean up orphaned `.tmp` files: `find <dir> -name '*.tmp' -mmin +60 -delete`.

---

## Failure 5 · MCP Server Not Connecting to Claude Desktop

**Symptoms:**
- Claude Desktop shows "MCP server not connected" or "Tool not available".
- `replicate-mcp-server` process exits immediately.

**Immediate Actions:**
1. Run the server manually and check stderr:
   ```bash
   REPLICATE_MCP_ENV=dev replicate-mcp-server 2>&1
   ```
2. Validate the Claude Desktop MCP config:
   ```json
   {
     "mcpServers": {
       "replicate": {
         "command": "replicate-mcp-server",
         "env": {"REPLICATE_API_TOKEN": "r..."}
       }
     }
   }
   ```

**Root Cause Steps:**
1. Verify `replicate-mcp-server` is in `PATH`: `which replicate-mcp-server`.
2. Check Python / Poetry environment: `poetry env info`.
3. Check MCP SDK version compatibility: `pip show mcp`.

**Remediation:**
- Ensure the virtualenv is activated in the Claude Desktop shell environment.
- Pin MCP SDK version: `mcp >=1.20.0,<2.0.0` (as per ADR-001).

---

## Failure 6 · Unsafe Expression in DSL Config

**Symptoms:**
- `InsecureConfigError: Forbidden eval/exec pattern detected`.
- Workflow fails to load YAML configuration.

**Immediate Actions:**
1. Identify the offending YAML file from the error message.
2. Remove or replace the `eval(...)` / `exec(...)` expression with a registered transform name.

**Root Cause Steps:**
1. Search for eval patterns: `grep -r 'eval\|exec\|__import__' examples/workflows/`.
2. Check if old YAML configs pre-date Sprint S1 (which fixed the eval vulnerability).

**Remediation:**
- Replace string lambda transforms with named transforms from `TransformRegistry`:
  ```yaml
  # Before (unsafe):
  transform: "eval('lambda d: d[\"enhanced_prompt\"]')"
  # After (safe):
  transform: extract_prompt
  ```
- Run `assert_no_eval_in_config()` on all YAML files during CI.

---

## Failure 7 · High Memory Usage / Memory Leak

**Symptoms:**
- Process memory grows unboundedly over hours.
- OOM killer terminates the process.

**Immediate Actions:**
1. Restart the MCP server: `kill -HUP <pid>` or restart the service.
2. Check for unbounded `TelemetryTracker` growth: `len(tracker.events)`.

**Root Cause Steps:**
1. Profile memory: `memray run replicate-mcp-server`.
2. Check `TelemetryTracker._events` — this list is unbounded in v0.3.
3. Check model discovery cache — capped at `limit=25` in `discover()`.

**Remediation:**
- Add a max-events cap to `TelemetryTracker` (Phase 3 item).
- Reduce checkpoint files: run `CheckpointManager.delete()` for completed sessions.
- Schedule periodic restart if memory usage exceeds 512 MB.

---

## Failure 8 · OpenTelemetry Collector Unreachable

**Symptoms:**
- Logs: `Failed to export spans to <endpoint>`.
- No data in Jaeger/Grafana dashboards.

**Immediate Actions:**
1. The system continues to operate — OTEL errors are caught and suppressed.
2. Verify the collector: `grpc_health_probe -addr=<otlp_endpoint>`.
3. Temporarily enable console fallback:
   ```python
   Observability(ObservabilityConfig(console_fallback=True))
   ```

**Root Cause Steps:**
1. Check `OTEL_EXPORTER_OTLP_ENDPOINT` env var.
2. Verify network connectivity to the collector.
3. Check collector resource limits (CPU/memory).

**Remediation:**
- Set `console_fallback=True` in `ObservabilityConfig` as a dev safety net.
- Use a local OTEL collector proxy to buffer spans during outages.

---

## Failure 9 · Workflow DAG Cycle Detected at Runtime

**Symptoms:**
- `CycleDetectedError: Cycle detected: a → b → c → a`.
- Workflow fails to start.

**Immediate Actions:**
1. Review the workflow definition for circular dependencies.
2. Use `AgentWorkflow.validate()` before executing:
   ```python
   issues = workflow.validate()
   if issues:
       print("\n".join(issues))
   ```

**Root Cause Steps:**
1. Visualise the DAG: print `workflow.edges` and draw the graph.
2. Check for accidental self-referencing edges.

**Remediation:**
- `add_edge()` performs cycle detection on every call — cycles are impossible
  if the API is used correctly.  This failure mode implies edges are being
  constructed outside the API (e.g. by deserialising untrusted YAML).
- Add schema validation to the YAML workflow loader (Phase 3 item).

---

## Failure 10 · Cost Tracking Divergence (> 10% vs Invoice)

**Symptoms:**
- Monthly Replicate invoice is more than 10 % higher than `TelemetryTracker.total_cost()`.

**Immediate Actions:**
1. Export telemetry events: `tracker.events` → CSV.
2. Compare with Replicate billing API (`GET /v1/predictions?...`).

**Root Cause Steps:**
1. Check for invocations that bypassed the executor (direct `replicate.run()` calls).
2. Check for invocations that failed before `record_invocation()` was called.
3. Check `estimated_cost` vs actual cost in `AgentMetadata`.

**Remediation:**
- Ensure all `replicate.run()` calls go through `AgentExecutor` (which always
  calls `_obs.record_invocation()`).
- Update `estimated_cost` values in `AgentMetadata` based on billing data.
- Phase 3: Pull actual cost from Replicate Prediction API and record it
  instead of the estimate.

---

## Escalation Path

```
Alert fires → On-call engineer (PagerDuty)
           → 15 min: Check this runbook
           → 30 min: Escalate to Eng Lead if unresolved
           → 1 hr:   Declare incident; open Slack war room #incident
           → 4 hr:   Escalate to Tech Lead; consider rollback
           → 48 hr:  Blameless postmortem (regardless of resolution time)
```

---

*Version 1.0 · Reviewed by SRE · 2026-04-20*