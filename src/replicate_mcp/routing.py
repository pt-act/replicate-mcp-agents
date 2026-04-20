"""Cost-aware model routing with EMA prediction and Thompson Sampling.

Sprint S7 — Hardening.  Routes agent invocations to the best available
Replicate model based on a weighted score of:

    * **Cost**    — predicted USD per invocation (EMA)
    * **Latency** — predicted milliseconds (EMA)
    * **Quality** — predicted quality score 0–1 (EMA)

Two selection strategies are supported:

``"score"`` (deterministic)
    Lower weighted-score wins.  Suitable for production workloads where
    consistency is preferred over exploration.

``"thompson"`` (stochastic)
    Thompson Sampling over a Beta(α, β) success/failure distribution.
    Balances exploration vs exploitation — under-tested models get a
    chance to prove themselves, while reliably-good models are preferred
    by default.

Design (see ADR-005):
    - All statistics are maintained per ``model`` string.
    - EMA smoothing factor ``alpha`` defaults to 0.3 (30% weight on new
      observation, 70% on historical average).
    - New models start with ``ts_alpha = ts_beta = 1`` (uniform prior).
    - ``record_outcome()`` must be called after every invocation so the
      router learns over time.

Usage::

    from replicate_mcp.routing import CostAwareRouter, RoutingWeights

    router = CostAwareRouter(weights=RoutingWeights(cost=0.5, latency=0.3, quality=0.2))
    router.register_model("meta/llama", initial_cost=0.002, initial_latency_ms=3000)
    router.register_model("mistral/mixtral", initial_cost=0.001, initial_latency_ms=2000)

    chosen = router.select_model(["meta/llama", "mistral/mixtral"])

    # After execution:
    router.record_outcome("meta/llama", latency_ms=3200, cost_usd=0.0021, success=True)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Per-model statistics
# ---------------------------------------------------------------------------


@dataclass
class ModelStats:
    """Running statistics for a single Replicate model.

    Attributes:
        model:            Full model identifier (``owner/name[:version]``).
        alpha:            EMA smoothing factor (0 < alpha ≤ 1).
        ema_latency_ms:   Exponential moving average of latency in ms.
        ema_cost_usd:     Exponential moving average of cost in USD.
        ema_quality:      Exponential moving average of quality score (0–1).
        invocation_count: Total number of invocations recorded.
        success_count:    Number of successful invocations.
        ts_alpha:         Beta distribution shape parameter (successes + 1).
        ts_beta:          Beta distribution shape parameter (failures + 1).
    """

    model: str
    alpha: float = 0.3
    ema_latency_ms: float = 5_000.0
    ema_cost_usd: float = 0.01
    ema_quality: float = 0.8
    invocation_count: int = 0
    success_count: int = 0
    ts_alpha: float = 1.0   # Beta prior — uniform
    ts_beta: float = 1.0

    def update(
        self,
        latency_ms: float,
        cost_usd: float,
        quality: float = 1.0,
        success: bool = True,
    ) -> None:
        """Incorporate a new observation into all EMA statistics."""
        a = self.alpha
        self.ema_latency_ms = a * latency_ms + (1 - a) * self.ema_latency_ms
        self.ema_cost_usd = a * cost_usd + (1 - a) * self.ema_cost_usd
        self.ema_quality = a * quality + (1 - a) * self.ema_quality
        self.invocation_count += 1
        if success:
            self.success_count += 1
            self.ts_alpha += 1.0
        else:
            self.ts_beta += 1.0

    def thompson_sample(self) -> float:
        """Draw one sample from Beta(ts_alpha, ts_beta).

        Returns a value in [0, 1] representing the model's sampled
        success probability.  Higher is better.
        """
        return random.betavariate(self.ts_alpha, self.ts_beta)

    @property
    def success_rate(self) -> float:
        """Empirical success rate (0–1); returns 1.0 if never invoked."""
        if self.invocation_count == 0:
            return 1.0
        return self.success_count / self.invocation_count

    def __repr__(self) -> str:
        return (
            f"ModelStats(model={self.model!r}, "
            f"ema_cost={self.ema_cost_usd:.4f}, "
            f"ema_latency={self.ema_latency_ms:.0f}ms, "
            f"success_rate={self.success_rate:.2%}, "
            f"invocations={self.invocation_count})"
        )


# ---------------------------------------------------------------------------
# Routing weights
# ---------------------------------------------------------------------------


@dataclass
class RoutingWeights:
    """Weights used by the score-based routing strategy.

    All three weights should sum to 1.0 for normalised scoring.
    They are not enforced to sum to 1 — the router normalises
    internally — but callers should be intentional about relative
    priorities.
    """

    cost: float = 0.4
    """Weight for predicted cost (higher = penalise expensive models more)."""

    latency: float = 0.3
    """Weight for predicted latency (higher = penalise slow models more)."""

    quality: float = 0.3
    """Weight for predicted quality (higher = prefer high-quality models more)."""

    def __post_init__(self) -> None:
        for name, val in (("cost", self.cost), ("latency", self.latency), ("quality", self.quality)):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"Weight '{name}' must be in [0, 1], got {val}")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class CostAwareRouter:
    """Select the optimal Replicate model from a candidate set.

    Supports two strategies:

    * ``"score"``    — weighted deterministic score (lower = better).
    * ``"thompson"`` — Thompson Sampling for explore-exploit balance.

    Args:
        weights:       :class:`RoutingWeights` for the score strategy.
        strategy:      ``"score"`` or ``"thompson"`` (default: ``"thompson"``).
        ema_alpha:     Default EMA smoothing factor for new models.
    """

    def __init__(
        self,
        weights: RoutingWeights | None = None,
        strategy: str = "thompson",
        ema_alpha: float = 0.3,
    ) -> None:
        if strategy not in ("score", "thompson"):
            raise ValueError(f"strategy must be 'score' or 'thompson', got '{strategy}'")
        self._weights = weights or RoutingWeights()
        self._strategy = strategy
        self._ema_alpha = ema_alpha
        self._stats: dict[str, ModelStats] = {}

    # ---- registration ----

    def register_model(
        self,
        model: str,
        *,
        initial_cost: float = 0.01,
        initial_latency_ms: float = 5_000.0,
        initial_quality: float = 0.8,
        alpha: float | None = None,
    ) -> None:
        """Register *model* with optional initial priors.

        If *model* is already registered, this is a no-op.
        """
        if model not in self._stats:
            self._stats[model] = ModelStats(
                model=model,
                alpha=alpha if alpha is not None else self._ema_alpha,
                ema_cost_usd=initial_cost,
                ema_latency_ms=initial_latency_ms,
                ema_quality=initial_quality,
            )

    def _ensure_registered(self, model: str) -> ModelStats:
        """Return stats for *model*, auto-registering if needed."""
        if model not in self._stats:
            self.register_model(model)
        return self._stats[model]

    # ---- selection ----

    def select_model(self, candidates: list[str]) -> str:
        """Return the best model from *candidates*.

        If *candidates* has one entry it is returned immediately.
        If *candidates* is empty a ``ValueError`` is raised.
        """
        if not candidates:
            raise ValueError("candidates list must not be empty")
        if len(candidates) == 1:
            return candidates[0]

        if self._strategy == "thompson":
            return self._thompson_select(candidates)
        return self._score_select(candidates)

    def _score_select(self, candidates: list[str]) -> str:
        """Weighted-score selection — lower score is better."""
        best_model = candidates[0]
        best_score = math.inf

        w = self._weights
        total_w = w.cost + w.latency + w.quality or 1.0

        for model in candidates:
            stats = self._ensure_registered(model)
            # Normalise latency to seconds for comparable scale with cost
            cost_s = stats.ema_cost_usd
            lat_s = stats.ema_latency_ms / 1_000.0
            qual_s = 1.0 - stats.ema_quality  # invert — lower is better

            score = (w.cost * cost_s + w.latency * lat_s + w.quality * qual_s) / total_w
            if score < best_score:
                best_score = score
                best_model = model

        return best_model

    def _thompson_select(self, candidates: list[str]) -> str:
        """Thompson Sampling selection — highest sampled success prob wins."""
        samples = {model: self._ensure_registered(model).thompson_sample()
                   for model in candidates}
        return max(samples, key=samples.__getitem__)

    # ---- feedback ----

    def record_outcome(
        self,
        model: str,
        *,
        latency_ms: float,
        cost_usd: float,
        success: bool = True,
        quality: float = 1.0,
    ) -> None:
        """Update statistics for *model* after an invocation completes.

        Args:
            model:       Model identifier (must match what was passed to
                         :meth:`select_model` or :meth:`register_model`).
            latency_ms:  Actual wall-clock latency in milliseconds.
            cost_usd:    Actual cost charged by Replicate in USD.
            success:     Whether the invocation succeeded.
            quality:     Application-defined quality score (0–1).
        """
        self._ensure_registered(model).update(
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            quality=quality,
            success=success,
        )

    # ---- introspection ----

    def stats(self) -> dict[str, ModelStats]:
        """Return a snapshot of statistics for all registered models."""
        return dict(self._stats)

    def leaderboard(self) -> list[tuple[str, float]]:
        """Return models sorted by EMA cost (cheapest first).

        Returns:
            List of ``(model, ema_cost_usd)`` tuples.
        """
        return sorted(
            [(m, s.ema_cost_usd) for m, s in self._stats.items()],
            key=lambda x: x[1],
        )

    @property
    def strategy(self) -> str:
        """The currently active selection strategy."""
        return self._strategy

    def __repr__(self) -> str:
        return (
            f"CostAwareRouter(strategy={self._strategy!r}, "
            f"models={list(self._stats)})"
        )


__all__ = [
    "ModelStats",
    "RoutingWeights",
    "CostAwareRouter",
]
