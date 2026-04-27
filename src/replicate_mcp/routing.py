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
    - EMA smoothing factor ``alpha`` configurable via ``ema_alpha`` parameter
      (default 0.3: 30% weight on new observation, 70% on historical average).
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
from typing import Any

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
    # Gaussian Thompson Sampling parameters for multi-objective utility
    utility_mu: float = 0.5   # Prior mean (normalized utility scale)
    utility_tau: float = 1.0  # Prior precision (1/variance)
    utility_sum: float = 0.0  # Running sum of utilities (for posterior)
    utility_sum_sq: float = 0.0  # Running sum of squared utilities

    def compute_scalar_utility(
        self,
        weights: RoutingWeights | None = None,
        max_cost: float = 0.1,
        max_latency_ms: float = 30_000.0,
    ) -> float:
        """Compute scalarized utility from EMA statistics (higher = better).

        Normalizes cost and latency to [0, 1] using max bounds,
        then computes weighted sum: utility = w_q * quality + w_c * (1 - cost/max_cost) + w_l * (1 - latency/max_latency)

        Args:
            weights: RoutingWeights for cost/latency/quality. Uses equal weights if None.
            max_cost: Upper bound for cost normalization (default: $0.10).
            max_latency_ms: Upper bound for latency normalization (default: 30s).

        Returns:
            Utility score in [0, 1], higher is better.
        """
        w = weights or RoutingWeights(cost=1.0 / 3, latency=1.0 / 3, quality=1.0 / 3)
        total = w.cost + w.latency + w.quality or 1.0

        # Normalize cost and latency to [0, 1], inverted (lower = better → higher score)
        cost_norm = max(0.0, 1.0 - (self.ema_cost_usd / max_cost))
        lat_norm = max(0.0, 1.0 - (self.ema_latency_ms / max_latency_ms))
        quality_norm = max(0.0, min(1.0, self.ema_quality))

        return (w.cost * cost_norm + w.latency * lat_norm + w.quality * quality_norm) / total

    def update(
        self,
        latency_ms: float,
        cost_usd: float,
        quality: float = 1.0,
        success: bool = True,
    ) -> float:
        """Incorporate a new observation into all EMA and utility statistics.

        Updates the Gaussian Thompson Sampling parameters for utility.

        Returns:
            The computed utility of this observation.
        """
        a = self.alpha
        self.ema_latency_ms = a * latency_ms + (1 - a) * self.ema_latency_ms
        self.ema_cost_usd = a * cost_usd + (1 - a) * self.ema_cost_usd
        self.ema_quality = a * quality + (1 - a) * self.ema_quality
        self.invocation_count += 1

        # Compute utility of this observation and update Gaussian parameters
        utility = self.compute_scalar_utility()
        if success:
            self.success_count += 1
            self.ts_alpha += 1.0
        else:
            self.ts_beta += 1.0
            # Penalize utility on failure
            utility *= 0.5

        # Update Gaussian Thompson Sampling statistics (for multi-objective)
        self.utility_sum += utility
        self.utility_sum_sq += utility * utility
        # Simple posterior update: precision increases with observations
        self.utility_tau += 1.0

        return utility

    def thompson_sample(self) -> float:
        """Draw one sample from Beta(ts_alpha, ts_beta).

        Returns a value in [0, 1] representing the model's sampled
        success probability.  Higher is better.
        """
        return random.betavariate(self.ts_alpha, self.ts_beta)

    def thompson_sample_utility(self, weights: RoutingWeights | None = None) -> float:
        """Draw one sample from the utility posterior (Gaussian Thompson Sampling).

        Uses a simple Gaussian approximation: mean = empirical mean of utilities,
        precision = prior precision + observation count. This incorporates
        cost, latency, and quality into the sampling decision.

        Args:
            weights: RoutingWeights for scalarization. Uses stored EMA weights if None.

        Returns:
            Sampled utility value (higher = better model).
        """
        if self.invocation_count == 0:
            # No observations: sample from prior
            return random.gauss(self.utility_mu, 1.0 / math.sqrt(self.utility_tau))

        # Empirical mean of observed utilities
        mean = self.utility_sum / self.invocation_count
        # Posterior precision increases with observations
        tau_post = self.utility_tau + self.invocation_count
        std = 1.0 / math.sqrt(tau_post)

        return random.gauss(mean, std)

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

    Supports three strategies:

    * ``"score"``          — weighted deterministic score (lower = better).
    * ``"thompson"``       — Beta Thompson Sampling (binary success/failure).
    * ``"thompson_multi"`` — Gaussian Thompson Sampling on scalarized utility
                              (incorporates cost, latency, quality).

    The ``"thompson_multi"`` strategy addresses the "Beta posterior conflates
    objectives" issue by using a multi-objective utility function instead of
    just binary success/failure.

    Args:
        weights:       :class:`RoutingWeights` for the score/multi-objective strategy.
        strategy:      ``"score"``, ``"thompson"``, or ``"thompson_multi"``.
        ema_alpha:     Default EMA smoothing factor for new models.
    """

    def __init__(
        self,
        weights: RoutingWeights | None = None,
        strategy: str = "thompson",
        ema_alpha: float = 0.3,
    ) -> None:
        if strategy not in ("score", "thompson", "thompson_multi"):
            raise ValueError(f"strategy must be 'score', 'thompson', or 'thompson_multi', got '{strategy}'")
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
        if self._strategy == "thompson_multi":
            return self._thompson_multi_select(candidates)
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

    def _thompson_multi_select(self, candidates: list[str]) -> str:
        """Multi-objective Thompson Sampling — highest sampled utility wins.

        Uses Gaussian Thompson Sampling on the scalarized utility that
        combines cost, latency, and quality (via compute_scalar_utility).
        This addresses the conflation issue where Beta-TS only considered
        binary success/failure.
        """
        samples = {
            model: self._ensure_registered(model).thompson_sample_utility(self._weights)
            for model in candidates
        }
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

    # ---- state synchronisation ----

    def sync_stats(
        self,
        model: str,
        *,
        ts_alpha: float,
        ts_beta: float,
    ) -> None:
        """Synchronise Thompson-Sampling posterior parameters for *model*.

        Copies ``ts_alpha`` and ``ts_beta`` from an external source
        (typically a parent router) without exposing ``_stats`` directly.
        This allows :class:`~replicate_mcp.qos.AdaptiveRouter` to keep its
        internal ``CostAwareRouter`` delegate in sync with the parent's Beta
        posterior without breaking encapsulation.

        Args:
            model:    Model identifier.  Auto-registered if not yet known.
            ts_alpha: New value for the Beta distribution's α parameter.
            ts_beta:  New value for the Beta distribution's β parameter.
        """
        stats = self._ensure_registered(model)
        stats.ts_alpha = ts_alpha
        stats.ts_beta = ts_beta

    # ---- introspection ----

    def stats(self) -> dict[str, ModelStats]:
        """Return a snapshot of statistics for all registered models."""
        return dict(self._stats)

    # ---- persistence helpers ----

    def dump_state(self) -> dict[str, Any]:
        """Serialise all per-model statistics to a JSON-safe dict.

        The returned structure is consumed by
        :class:`~replicate_mcp.utils.router_state.RouterStateManager` and can
        be round-tripped through :meth:`load_state`.

        Returns:
            Mapping of model identifier → serialised :class:`ModelStats` dict.
        """
        from replicate_mcp.utils.router_state import serialise_stats  # noqa: PLC0415

        return {model: serialise_stats(s) for model, s in self._stats.items()}

    def load_state(self, data: dict[str, Any]) -> None:
        """Restore per-model statistics from a previously :meth:`dump_state` dict.

        Existing entries are overwritten; models absent from *data* are
        untouched.  Unknown keys inside each model dict are silently ignored
        for forward compatibility.

        Args:
            data: Mapping of model identifier → raw stats dict, as produced by
                  :meth:`dump_state`.
        """
        from replicate_mcp.utils.router_state import deserialise_stats  # noqa: PLC0415

        for model, raw in data.items():
            try:
                self._stats[model] = deserialise_stats(raw)
            except (TypeError, KeyError) as exc:
                import logging  # noqa: PLC0415

                logging.getLogger(__name__).warning(
                    "Could not restore router state for model %r: %s", model, exc
                )

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

    def select_model_explain(self, candidates: list[str]) -> RoutingDecision:
        """Select the best model and return a detailed explanation.

        Like :meth:`select_model` but returns a :class:`RoutingDecision`
        that captures *why* the model was chosen, including scores for
        all candidates.

        Args:
            candidates: Model identifiers to choose from.

        Returns:
            A :class:`RoutingDecision` with the chosen model and scores.
        """
        if not candidates:
            raise ValueError("candidates list must not be empty")
        if len(candidates) == 1:
            return RoutingDecision(
                selected_model=candidates[0],
                strategy=self._strategy,
                scores={candidates[0]: 0.0},
            )

        if self._strategy == "thompson":
            samples = {model: self._ensure_registered(model).thompson_sample()
                       for model in candidates}
            selected = max(samples, key=samples.__getitem__)
            return RoutingDecision(
                selected_model=selected,
                strategy=self._strategy,
                scores=samples,
            )

        # Score-based
        w = self._weights
        total_w = w.cost + w.latency + w.quality or 1.0
        scores: dict[str, float] = {}
        for model in candidates:
            stats = self._ensure_registered(model)
            cost_s = stats.ema_cost_usd
            lat_s = stats.ema_latency_ms / 1_000.0
            qual_s = 1.0 - stats.ema_quality
            scores[model] = (w.cost * cost_s + w.latency * lat_s + w.quality * qual_s) / total_w
        selected = min(scores, key=scores.__getitem__)  # type: ignore[arg-type]
        return RoutingDecision(
            selected_model=selected,
            strategy=self._strategy,
            scores=scores,
        )

    def __repr__(self) -> str:
        return (
            f"CostAwareRouter(strategy={self._strategy!r}, "
            f"models={list(self._stats)})"
        )


@dataclass
class RoutingDecision:
    """Explains *why* a model was selected by :meth:`CostAwareRouter.select_model_explain`.

    Attributes:
        selected_model: The model identifier that was chosen.
        strategy:       The selection strategy used (``"score"`` or ``"thompson"``).
        scores:         Per-model score / sample used for the decision.
                        For ``"score"`` strategy these are weighted scores (lower = better).
                        For ``"thompson"`` strategy these are Thompson samples (higher = better).
    """

    selected_model: str
    strategy: str
    scores: dict[str, float]

    def __repr__(self) -> str:
        return (
            f"RoutingDecision(selected={self.selected_model!r}, "
            f"strategy={self.strategy!r}, "
            f"scores={self.scores})"
        )


__all__ = [
    "ModelStats",
    "RoutingWeights",
    "CostAwareRouter",
    "RoutingDecision",
]
