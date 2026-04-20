"""Tests for replicate_mcp.qos — QoS tiers, UCB1 router, AdaptiveRouter."""

from __future__ import annotations

import math

import pytest

from replicate_mcp.qos import (
    AdaptiveRouter,
    QoSLevel,
    QoSPolicy,
    UCB1Router,
)
from replicate_mcp.routing import ModelStats

# ---------------------------------------------------------------------------
# QoSLevel
# ---------------------------------------------------------------------------


class TestQoSLevel:
    def test_values(self) -> None:
        assert QoSLevel.FAST == "fast"
        assert QoSLevel.BALANCED == "balanced"
        assert QoSLevel.QUALITY == "quality"


# ---------------------------------------------------------------------------
# QoSPolicy
# ---------------------------------------------------------------------------


class TestQoSPolicy:
    def test_for_level_fast(self) -> None:
        p = QoSPolicy.for_level(QoSLevel.FAST)
        assert p.max_latency_ms == 2_000.0
        assert p.min_quality == 0.5
        assert p.level == QoSLevel.FAST

    def test_for_level_balanced(self) -> None:
        p = QoSPolicy.for_level(QoSLevel.BALANCED)
        assert p.max_latency_ms == 5_000.0
        assert p.max_cost_usd == 0.05
        assert p.min_quality == 0.7

    def test_for_level_quality(self) -> None:
        p = QoSPolicy.for_level(QoSLevel.QUALITY)
        assert p.min_quality == 0.9
        assert p.max_cost_usd == 0.10

    def test_passes_no_constraints(self) -> None:
        p = QoSPolicy()
        stats = ModelStats(model="m", ema_latency_ms=99_999, ema_cost_usd=100, ema_quality=0.0)
        assert p.passes(stats)

    def test_fails_latency(self) -> None:
        p = QoSPolicy(max_latency_ms=1_000)
        stats = ModelStats(model="m", ema_latency_ms=2_000)
        assert not p.passes(stats)

    def test_fails_cost(self) -> None:
        p = QoSPolicy(max_cost_usd=0.01)
        stats = ModelStats(model="m", ema_cost_usd=0.02)
        assert not p.passes(stats)

    def test_fails_quality(self) -> None:
        p = QoSPolicy(min_quality=0.9)
        stats = ModelStats(model="m", ema_quality=0.5)
        assert not p.passes(stats)

    def test_fails_success_rate(self) -> None:
        p = QoSPolicy(min_success_rate=0.8)
        stats = ModelStats(model="m", invocation_count=10, success_count=5)
        assert not p.passes(stats)

    def test_passes_all_constraints(self) -> None:
        p = QoSPolicy(
            max_latency_ms=5_000,
            max_cost_usd=0.05,
            min_quality=0.7,
            min_success_rate=0.8,
        )
        stats = ModelStats(
            model="m",
            ema_latency_ms=1_000,
            ema_cost_usd=0.01,
            ema_quality=0.95,
            invocation_count=10,
            success_count=9,
        )
        assert p.passes(stats)

    def test_filter_candidates_returns_passing(self) -> None:
        p = QoSPolicy(max_latency_ms=2_000)
        stats_map = {
            "fast": ModelStats(model="fast", ema_latency_ms=500),
            "slow": ModelStats(model="slow", ema_latency_ms=9_000),
        }
        result = p.filter_candidates(["fast", "slow"], stats_map)
        assert result == ["fast"]

    def test_filter_candidates_graceful_degradation(self) -> None:
        """If all fail, return the full list."""
        p = QoSPolicy(max_latency_ms=1)
        stats_map = {
            "a": ModelStats(model="a", ema_latency_ms=5_000),
            "b": ModelStats(model="b", ema_latency_ms=5_000),
        }
        result = p.filter_candidates(["a", "b"], stats_map)
        assert set(result) == {"a", "b"}

    def test_filter_candidates_empty_stats_map(self) -> None:
        p = QoSPolicy(max_latency_ms=1_000)
        result = p.filter_candidates(["a", "b"], {})
        assert set(result) == {"a", "b"}


# ---------------------------------------------------------------------------
# UCB1Router
# ---------------------------------------------------------------------------


class TestUCB1Router:
    def test_unvisited_models_selected_first(self) -> None:
        router = UCB1Router()
        router.register_model("a")
        router.register_model("b")
        # Both unvisited → any selection is valid
        choice = router.select_model(["a", "b"])
        assert choice in ("a", "b")

    def test_single_candidate_returned_directly(self) -> None:
        router = UCB1Router()
        assert router.select_model(["only"]) == "only"

    def test_empty_candidates_raises(self) -> None:
        router = UCB1Router()
        with pytest.raises(ValueError):
            router.select_model([])

    def test_ucb1_score_unvisited_is_inf(self) -> None:
        router = UCB1Router()
        stats = ModelStats(model="m", invocation_count=0)
        assert router._ucb1_score(stats, total=10) == math.inf

    def test_ucb1_score_visited(self) -> None:
        router = UCB1Router()
        stats = ModelStats(
            model="m",
            invocation_count=5,
            success_count=4,
        )
        score = router._ucb1_score(stats, total=20)
        assert score > 0
        assert score < math.inf

    def test_exploration_constant_scales_score(self) -> None:
        stats = ModelStats(model="m", invocation_count=5, success_count=4)
        r1 = UCB1Router(exploration_c=1.0)
        r2 = UCB1Router(exploration_c=2.0)
        s1 = r1._ucb1_score(stats, total=20)
        s2 = r2._ucb1_score(stats, total=20)
        assert s2 > s1

    def test_record_outcome_increments_total(self) -> None:
        router = UCB1Router()
        router.register_model("m")
        assert router._total_invocations == 0
        router.record_outcome("m", latency_ms=100, cost_usd=0.001)
        assert router._total_invocations == 1

    def test_select_model_with_policy(self) -> None:
        router = UCB1Router()
        router.register_model("fast", initial_latency_ms=500)
        router.register_model("slow", initial_latency_ms=9_000)
        policy = QoSPolicy.for_level(QoSLevel.FAST)
        chosen = router.select_model_with_policy(["fast", "slow"], policy=policy)
        assert chosen == "fast"

    def test_select_model_with_policy_graceful(self) -> None:
        """All candidates fail → graceful degradation."""
        router = UCB1Router()
        router.register_model("a", initial_latency_ms=9_000)
        router.register_model("b", initial_latency_ms=9_000)
        policy = QoSPolicy.for_level(QoSLevel.FAST)
        # Should not raise
        chosen = router.select_model_with_policy(["a", "b"], policy=policy)
        assert chosen in ("a", "b")

    def test_prefers_model_with_higher_success_rate_after_many_trials(self) -> None:
        """After enough trials, UCB1 should prefer the better model."""
        router = UCB1Router(exploration_c=0.1)  # Low exploration
        router.register_model("good")
        router.register_model("bad")

        # Train "good" with 20 successes
        for _ in range(20):
            router.record_outcome("good", latency_ms=100, cost_usd=0.001, success=True)
        # Train "bad" with 10 failures
        for _ in range(10):
            router.record_outcome("bad", latency_ms=100, cost_usd=0.001, success=False)

        choices = [router.select_model(["good", "bad"]) for _ in range(10)]
        assert choices.count("good") > choices.count("bad")


# ---------------------------------------------------------------------------
# AdaptiveRouter
# ---------------------------------------------------------------------------


class TestAdaptiveRouter:
    def test_active_strategy_starts_as_ucb1(self) -> None:
        router = AdaptiveRouter(explore_threshold=10)
        assert router.active_strategy == "ucb1"

    def test_switches_to_thompson_after_threshold(self) -> None:
        router = AdaptiveRouter(explore_threshold=5)
        router.register_model("m")
        for _ in range(5):
            router.record_outcome("m", latency_ms=100, cost_usd=0.001)
        assert router.active_strategy == "thompson"

    def test_selects_using_ucb1_before_threshold(self) -> None:
        router = AdaptiveRouter(explore_threshold=100)
        router.register_model("a")
        router.register_model("b")
        # With low total invocations, unvisited models get +inf → must be tried
        choice = router.select_model(["a", "b"])
        assert choice in ("a", "b")

    def test_selects_using_thompson_after_threshold(self) -> None:
        router = AdaptiveRouter(explore_threshold=1)
        router.register_model("a")
        router.register_model("b")
        router.record_outcome("a", latency_ms=100, cost_usd=0.001, success=True)
        assert router.active_strategy == "thompson"
        # Should not raise
        choice = router.select_model(["a", "b"])
        assert choice in ("a", "b")

    def test_single_candidate(self) -> None:
        router = AdaptiveRouter()
        assert router.select_model(["only"]) == "only"

    def test_empty_candidates_raises(self) -> None:
        router = AdaptiveRouter()
        with pytest.raises(ValueError):
            router.select_model([])
