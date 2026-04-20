"""Tests for replicate_mcp.routing — cost-aware model router."""

from __future__ import annotations

import pytest

from replicate_mcp.routing import CostAwareRouter, ModelStats, RoutingWeights

# ---------------------------------------------------------------------------
# ModelStats
# ---------------------------------------------------------------------------


class TestModelStats:
    def test_initial_values(self) -> None:
        stats = ModelStats(model="test/model")
        assert stats.invocation_count == 0
        assert stats.success_count == 0
        assert stats.success_rate == 1.0  # no invocations yet → 1.0

    def test_update_success(self) -> None:
        stats = ModelStats(model="test/model", alpha=1.0)
        stats.update(latency_ms=1000, cost_usd=0.01, quality=0.9, success=True)
        assert stats.invocation_count == 1
        assert stats.success_count == 1
        assert stats.ema_latency_ms == pytest.approx(1000.0)
        assert stats.ema_cost_usd == pytest.approx(0.01)

    def test_update_failure(self) -> None:
        stats = ModelStats(model="test/model")
        stats.update(latency_ms=500, cost_usd=0.005, success=False)
        assert stats.invocation_count == 1
        assert stats.success_count == 0
        assert stats.success_rate == 0.0

    def test_ema_smoothing(self) -> None:
        stats = ModelStats(model="m", alpha=0.5, ema_latency_ms=1000)
        stats.update(latency_ms=2000, cost_usd=0.01, success=True)
        # 0.5 * 2000 + 0.5 * 1000 = 1500
        assert stats.ema_latency_ms == pytest.approx(1500.0)

    def test_thompson_sample_in_range(self) -> None:
        stats = ModelStats(model="m")
        for _ in range(100):
            s = stats.thompson_sample()
            assert 0.0 <= s <= 1.0

    def test_repr(self) -> None:
        stats = ModelStats(model="owner/model")
        assert "ModelStats" in repr(stats)
        assert "owner/model" in repr(stats)

    def test_success_rate_with_invocations(self) -> None:
        stats = ModelStats(model="m")
        stats.update(1000, 0.01, success=True)
        stats.update(1000, 0.01, success=False)
        assert stats.success_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# RoutingWeights
# ---------------------------------------------------------------------------


class TestRoutingWeights:
    def test_default_weights(self) -> None:
        w = RoutingWeights()
        assert w.cost == pytest.approx(0.4)
        assert w.latency == pytest.approx(0.3)
        assert w.quality == pytest.approx(0.3)

    def test_invalid_weight_negative(self) -> None:
        with pytest.raises(ValueError):
            RoutingWeights(cost=-0.1)

    def test_invalid_weight_greater_than_one(self) -> None:
        with pytest.raises(ValueError):
            RoutingWeights(latency=1.5)


# ---------------------------------------------------------------------------
# CostAwareRouter — score strategy
# ---------------------------------------------------------------------------


class TestCostAwareRouterScore:
    def _make_router(self) -> CostAwareRouter:
        return CostAwareRouter(strategy="score", weights=RoutingWeights(cost=1.0, latency=0.0, quality=0.0))

    def test_single_candidate_returned_immediately(self) -> None:
        r = self._make_router()
        assert r.select_model(["only/model"]) == "only/model"

    def test_empty_candidates_raises(self) -> None:
        r = self._make_router()
        with pytest.raises(ValueError, match="empty"):
            r.select_model([])

    def test_cheaper_model_selected(self) -> None:
        r = self._make_router()
        r.register_model("expensive/model", initial_cost=0.10)
        r.register_model("cheap/model", initial_cost=0.001)
        chosen = r.select_model(["expensive/model", "cheap/model"])
        assert chosen == "cheap/model"

    def test_auto_register_on_select(self) -> None:
        r = self._make_router()
        # Should not raise even if models weren't pre-registered
        chosen = r.select_model(["a/model", "b/model"])
        assert chosen in ("a/model", "b/model")

    def test_record_outcome_updates_ema(self) -> None:
        r = self._make_router()
        r.register_model("m/model", initial_cost=0.01)
        r.record_outcome("m/model", latency_ms=1000, cost_usd=0.001, success=True)
        stats = r.stats()["m/model"]
        # After one update with alpha=0.3:  0.3 * 0.001 + 0.7 * 0.01 = 0.0073
        assert stats.ema_cost_usd < 0.01

    def test_stats_returns_snapshot(self) -> None:
        r = self._make_router()
        r.register_model("a/model")
        snap = r.stats()
        assert "a/model" in snap

    def test_leaderboard_sorted_by_cost(self) -> None:
        r = self._make_router()
        r.register_model("expensive/x", initial_cost=0.10)
        r.register_model("medium/x", initial_cost=0.05)
        r.register_model("cheap/x", initial_cost=0.001)
        board = r.leaderboard()
        costs = [c for _, c in board]
        assert costs == sorted(costs)

    def test_strategy_property(self) -> None:
        r = self._make_router()
        assert r.strategy == "score"

    def test_repr(self) -> None:
        r = self._make_router()
        assert "CostAwareRouter" in repr(r)


# ---------------------------------------------------------------------------
# CostAwareRouter — Thompson Sampling strategy
# ---------------------------------------------------------------------------


class TestCostAwareRouterThompson:
    def _make_router(self) -> CostAwareRouter:
        return CostAwareRouter(strategy="thompson")

    def test_thompson_selects_from_candidates(self) -> None:
        r = self._make_router()
        candidates = ["a/model", "b/model", "c/model"]
        chosen = r.select_model(candidates)
        assert chosen in candidates

    def test_thompson_favours_high_success_model(self) -> None:
        """After many successes, the good model should win most of the time."""
        r = self._make_router()
        r.register_model("good/model", initial_cost=0.01)
        r.register_model("bad/model", initial_cost=0.01)
        for _ in range(50):
            r.record_outcome("good/model", latency_ms=100, cost_usd=0.01, success=True)
        for _ in range(50):
            r.record_outcome("bad/model", latency_ms=100, cost_usd=0.01, success=False)

        wins = sum(
            1 for _ in range(200)
            if r.select_model(["good/model", "bad/model"]) == "good/model"
        )
        # Good model should win the vast majority of the time
        assert wins > 140

    def test_invalid_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="strategy"):
            CostAwareRouter(strategy="invalid")

    def test_register_model_idempotent(self) -> None:
        r = self._make_router()
        r.register_model("a/model")
        r.register_model("a/model")  # second call should be no-op
        assert "a/model" in r.stats()
