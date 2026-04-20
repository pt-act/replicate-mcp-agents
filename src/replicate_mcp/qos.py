"""Quality-of-Service routing policies for multi-model orchestration.

Sprint S10 — Differentiation.  Extends the base
:class:`~replicate_mcp.routing.CostAwareRouter` with:

* :class:`QoSLevel` — three service tiers (FAST / BALANCED / QUALITY).
* :class:`QoSPolicy` — per-level SLA constraints (latency cap, cost cap,
  minimum quality floor).
* :class:`UCB1Router` — Upper Confidence Bound 1 bandit algorithm as a
  deterministic alternative to Thompson Sampling.  Useful for batch
  workloads where reproducibility is preferred over exploration.
* :class:`AdaptiveRouter` — meta-router that selects between UCB1 and
  Thompson Sampling based on observed reward variance.

Design:
    - QoS tiers do *not* replace the existing routing strategies; they
      act as a *pre-filter*: candidates failing the SLA are removed
      before the strategy is applied.
    - UCB1 is parameter-free (no prior distributions) and has a
      well-understood theoretical regret bound of O(√(n log n)).
    - Both routers share the :class:`~replicate_mcp.routing.ModelStats`
      data structure for interoperability.

Usage::

    from replicate_mcp.qos import QoSLevel, QoSPolicy, UCB1Router

    router = UCB1Router()
    router.register_model("meta/llama", initial_cost=0.002)
    router.register_model("mistral/mixtral", initial_cost=0.001)

    # Enforce FAST tier: prefer models with < 2 s latency
    policy = QoSPolicy.for_level(QoSLevel.FAST)
    chosen = router.select_model_with_policy(
        ["meta/llama", "mistral/mixtral"], policy=policy
    )
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from replicate_mcp.routing import CostAwareRouter, ModelStats, RoutingWeights

# ---------------------------------------------------------------------------
# QoS tiers
# ---------------------------------------------------------------------------


class QoSLevel(str, Enum):
    """Service-quality tier for routing decisions.

    Attributes:
        FAST:     Optimise for lowest latency; accept lower quality.
        BALANCED: Balance cost, latency, and quality equally.
        QUALITY:  Optimise for highest quality; accept higher cost/latency.
    """

    FAST = "fast"
    BALANCED = "balanced"
    QUALITY = "quality"


@dataclass
class QoSPolicy:
    """SLA constraints applied as a pre-filter before model selection.

    Attributes:
        max_latency_ms:  Reject models whose EMA latency exceeds this.
                         ``None`` means no constraint.
        max_cost_usd:    Reject models whose EMA cost exceeds this.
                         ``None`` means no constraint.
        min_quality:     Reject models whose EMA quality is below this.
                         ``None`` means no constraint.
        min_success_rate: Reject models below this empirical success rate.
                         ``None`` means no constraint.
        level:           The :class:`QoSLevel` this policy represents.
    """

    max_latency_ms: float | None = None
    max_cost_usd: float | None = None
    min_quality: float | None = None
    min_success_rate: float | None = None
    level: QoSLevel = QoSLevel.BALANCED

    # ---- factory ----

    @classmethod
    def for_level(cls, level: QoSLevel) -> QoSPolicy:
        """Return a sensible default policy for the given QoS tier.

        Default SLA constraints:

        * ``FAST``     → latency < 2 000 ms, quality ≥ 0.5
        * ``BALANCED`` → latency < 5 000 ms, cost < $0.05, quality ≥ 0.7
        * ``QUALITY``  → quality ≥ 0.9, cost < $0.10
        """
        if level == QoSLevel.FAST:
            return cls(
                max_latency_ms=2_000.0,
                min_quality=0.5,
                level=level,
            )
        if level == QoSLevel.QUALITY:
            return cls(
                min_quality=0.9,
                max_cost_usd=0.10,
                level=level,
            )
        # BALANCED
        return cls(
            max_latency_ms=5_000.0,
            max_cost_usd=0.05,
            min_quality=0.7,
            level=level,
        )

    # ---- filtering ----

    def passes(self, stats: ModelStats) -> bool:
        """Return ``True`` if *stats* satisfies all constraints."""
        if self.max_latency_ms is not None and stats.ema_latency_ms > self.max_latency_ms:
            return False
        if self.max_cost_usd is not None and stats.ema_cost_usd > self.max_cost_usd:
            return False
        if self.min_quality is not None and stats.ema_quality < self.min_quality:
            return False
        if self.min_success_rate is not None and stats.success_rate < self.min_success_rate:
            return False
        return True

    def filter_candidates(
        self,
        candidates: list[str],
        stats_map: dict[str, ModelStats],
    ) -> list[str]:
        """Return the subset of *candidates* that pass this policy.

        If *all* candidates fail, the full list is returned unchanged
        (graceful degradation — never return an empty set).
        """
        passing = [m for m in candidates if self.passes(stats_map[m])] if stats_map else []
        return passing if passing else list(candidates)


# ---------------------------------------------------------------------------
# UCB1 router
# ---------------------------------------------------------------------------


class UCB1Router(CostAwareRouter):
    """UCB1 bandit router: explores under-tested models systematically.

    Upper Confidence Bound 1 (UCB1) selects the model that maximises::

        μ_i + √(2 · ln(N) / n_i)

    where ``μ_i`` is the model's empirical success rate, ``N`` is the
    total number of invocations across all models, and ``n_i`` is the
    number of invocations for model ``i``.

    UCB1 guarantees that every model is tried O(log N) times, avoiding
    the starvation problem in pure-greedy approaches, while achieving a
    tight regret bound of O(√(K · N · log N)) where K is the number of
    models.

    Args:
        exploration_c: Scaling factor for the exploration bonus.
                       Higher values encourage more exploration.
                       Default: 1.0 (standard UCB1).
        weights:       Passed through to the score strategy when UCB1
                       falls back to score selection (tie-breaking).
    """

    def __init__(
        self,
        *,
        exploration_c: float = 1.0,
        weights: RoutingWeights | None = None,
    ) -> None:
        super().__init__(weights=weights, strategy="score")
        self._exploration_c = exploration_c
        self._total_invocations: int = 0

    def _ucb1_score(self, stats: ModelStats, total: int) -> float:
        """Compute the UCB1 index for a single model.

        Higher is better (unlike the score strategy where lower is better).
        Unvisited models receive ``+inf`` to ensure they are tried first.
        """
        n = stats.invocation_count
        if n == 0:
            return math.inf
        mu = stats.success_rate
        exploration_bonus = self._exploration_c * math.sqrt(2 * math.log(total) / n)
        return mu + exploration_bonus

    def select_model(self, candidates: list[str]) -> str:  # type: ignore[override]
        """UCB1 selection: return the candidate with the highest UCB1 index.

        Falls back to the parent's strategy when only one candidate
        is provided.
        """
        if not candidates:
            raise ValueError("candidates list must not be empty")
        if len(candidates) == 1:
            return candidates[0]

        total = max(
            1,
            sum(
                self._ensure_registered(m).invocation_count
                for m in candidates
            ),
        )
        return max(
            candidates,
            key=lambda m: self._ucb1_score(self._ensure_registered(m), total),
        )

    def record_outcome(  # type: ignore[override]
        self,
        model: str,
        *,
        latency_ms: float,
        cost_usd: float,
        success: bool = True,
        quality: float = 1.0,
    ) -> None:
        """Record an outcome and update the total-invocations counter."""
        super().record_outcome(
            model,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            success=success,
            quality=quality,
        )
        self._total_invocations += 1

    def select_model_with_policy(
        self,
        candidates: list[str],
        *,
        policy: QoSPolicy,
    ) -> str:
        """Select the best model after applying a :class:`QoSPolicy` filter.

        Models that violate the policy's SLA constraints are excluded.
        If all candidates are filtered out, the full set is used
        (graceful degradation).

        Args:
            candidates: Pool of candidate model identifiers.
            policy:     QoS policy to apply as a pre-filter.

        Returns:
            The selected model identifier.
        """
        stats_snapshot = self._stats
        filtered = policy.filter_candidates(candidates, stats_snapshot)
        return self.select_model(filtered)


# ---------------------------------------------------------------------------
# Adaptive router
# ---------------------------------------------------------------------------


class AdaptiveRouter(UCB1Router):
    """Meta-router that switches between UCB1 and Thompson Sampling.

    During early exploration (fewer than ``explore_threshold`` total
    invocations), UCB1 is used to systematically test all candidates.
    After the threshold is crossed, Thompson Sampling takes over to
    exploit the accumulated knowledge.

    This gives the best of both algorithms:

    * UCB1's theoretical regret guarantees during cold start.
    * Thompson Sampling's empirical superiority once priors are informed.

    Args:
        explore_threshold: Minimum total invocations before switching
                           to Thompson Sampling.
        exploration_c:     UCB1 exploration constant.
        weights:           Weights for deterministic score fallback.
    """

    def __init__(
        self,
        *,
        explore_threshold: int = 20,
        exploration_c: float = 1.0,
        weights: RoutingWeights | None = None,
    ) -> None:
        super().__init__(exploration_c=exploration_c, weights=weights)
        self._explore_threshold = explore_threshold
        # Re-expose Thompson strategy from parent class logic.
        self._ts_router = CostAwareRouter(weights=weights, strategy="thompson")

    @property
    def active_strategy(self) -> str:
        """Return the name of the currently active routing strategy."""
        if self._total_invocations < self._explore_threshold:
            return "ucb1"
        return "thompson"

    def select_model(self, candidates: list[str]) -> str:  # type: ignore[override]
        """Route using UCB1 or Thompson Sampling depending on phase."""
        if not candidates:
            raise ValueError("candidates list must not be empty")
        if len(candidates) == 1:
            return candidates[0]

        if self._total_invocations < self._explore_threshold:
            # Exploration phase — use UCB1
            return super().select_model(candidates)

        # Exploitation phase — use Thompson Sampling via shared stats
        for m in candidates:
            stats = self._ensure_registered(m)
            if m not in self._ts_router._stats:  # noqa: SLF001
                self._ts_router.register_model(
                    m,
                    initial_cost=stats.ema_cost_usd,
                    initial_latency_ms=stats.ema_latency_ms,
                    initial_quality=stats.ema_quality,
                )
            # Sync posterior parameters
            ts_stats = self._ts_router._stats[m]  # noqa: SLF001
            ts_stats.ts_alpha = stats.ts_alpha
            ts_stats.ts_beta = stats.ts_beta

        return self._ts_router.select_model(candidates)


__all__ = [
    "QoSLevel",
    "QoSPolicy",
    "UCB1Router",
    "AdaptiveRouter",
]
