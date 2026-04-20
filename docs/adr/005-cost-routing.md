# ADR-005 · Cost-Aware Model Routing with EMA + Thompson Sampling

**Status:** Accepted  
**Sprint:** S7  
**Date:** 2026-04-20  
**Deciders:** Eng Lead, PM  

---

## Context

When multiple Replicate models can serve the same task (e.g. several LLMs
with comparable capability but different price/latency/quality trade-offs),
we need a principled way to pick the best one.

Static routing (always use the cheapest, or always use the fastest) is
brittle:
- Cheapest model may be down or slow.
- Fastest model may have degraded quality.
- New models should be explored without over-committing.

We evaluated three routing strategies:

| Strategy | Description | Tradeoff |
|----------|-------------|----------|
| Weighted score | Deterministic; weighted sum of EMA cost, latency, quality | Greedy; never explores |
| ε-greedy | Exploits best with probability 1-ε; random with ε | ε requires tuning; crude exploration |
| Thompson Sampling | Bayesian; samples from Beta posterior | Principled; adapts automatically |

## Decision

Implement **`src/replicate_mcp/routing.py`** with two strategies:

### Statistics: Exponential Moving Average (EMA)

For each model, maintain running estimates using EMA with configurable
`alpha` (default 0.3):

```
EMA_new = alpha * observation + (1 - alpha) * EMA_old
```

Three dimensions tracked:
- `ema_cost_usd` — predicted USD cost per invocation
- `ema_latency_ms` — predicted latency
- `ema_quality` — predicted quality score [0, 1]

### Selection: Thompson Sampling (default)

Maintain a Beta distribution `Beta(successes+1, failures+1)` per model.
At selection time, draw one sample from each candidate's distribution.
The highest-sampled model wins.

Properties:
- Naturally balances exploration vs exploitation.
- Converges to the best model over time.
- New models (uniform prior) get explored fairly.
- Catastrophic failure (many negatives) causes model to lose selections.

### Selection: Weighted Score (deterministic fallback)

```
score = (w_cost * ema_cost + w_latency * ema_latency/1000 + w_quality * (1 - ema_quality))
        / (w_cost + w_latency + w_quality)
```

Lowest score wins.  Configurable via `RoutingWeights`.

### Integration

`CostAwareRouter` is instantiated in `server.py` as a module-level
singleton.  Every registered agent's model is pre-registered with the router.
The `record_outcome()` call is made by `AgentExecutor` after each invocation.
The `routing://leaderboard` MCP resource exposes current cost rankings.

## Consequences

**Positive:**
- Automatically learns which models are cheapest/fastest/most reliable.
- New Replicate models can be added and will be explored automatically.
- Fully unit-testable (no external API calls required).

**Negative:**
- Router state is in-memory; lost on restart.  Deferred to Phase 3 (persistent state).
- Thompson Sampling requires sufficient history to converge.  With <10 invocations
  the selection is mostly random (which is fine — it's exploration).

**Risks & Mitigations:**
- Model version changes invalidate historical stats:
  version-aware cache keys deferred to Phase 3.
- Thompson Sampling with beta distribution assumes Bernoulli success:
  adequate for binary success/failure but not quality scores.
  Quality-aware routing (multi-armed bandit on quality) is a Phase 3 item.