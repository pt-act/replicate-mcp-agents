"""Tests for replicate_mcp.routing — cost-aware model router."""

from __future__ import annotations

import math

import pytest

from replicate_mcp.routing import (
    CostAwareRouter,
    ModelStats,
    RoutingDecision,
    RoutingWeights,
)

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
        return CostAwareRouter(
            strategy="score", weights=RoutingWeights(cost=1.0, latency=0.0, quality=0.0)
        )

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
            1 for _ in range(200) if r.select_model(["good/model", "bad/model"]) == "good/model"
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


# ---------------------------------------------------------------------------
# Phase 4 — sync_stats() encapsulation fix
# ---------------------------------------------------------------------------


class TestSyncStats:
    def test_sync_stats_updates_ts_params(self) -> None:
        r = CostAwareRouter()
        r.register_model("m/1")
        r.sync_stats("m/1", ts_alpha=5.0, ts_beta=2.0)
        s = r.stats()["m/1"]
        assert s.ts_alpha == 5.0
        assert s.ts_beta == 2.0

    def test_sync_stats_auto_registers(self) -> None:
        """sync_stats should auto-register an unknown model."""
        r = CostAwareRouter()
        r.sync_stats("new/model", ts_alpha=3.0, ts_beta=7.0)
        assert "new/model" in r.stats()
        s = r.stats()["new/model"]
        assert s.ts_alpha == 3.0
        assert s.ts_beta == 7.0

    def test_sync_stats_does_not_touch_other_fields(self) -> None:
        r = CostAwareRouter()
        r.register_model("m/2", initial_cost=0.005, initial_latency_ms=1000)
        r.sync_stats("m/2", ts_alpha=9.0, ts_beta=1.0)
        s = r.stats()["m/2"]
        assert s.ema_cost_usd == pytest.approx(0.005)
        assert s.ema_latency_ms == pytest.approx(1000)
        assert s.ts_alpha == pytest.approx(9.0)

    def test_adaptive_router_uses_sync_stats_not_private_access(self) -> None:
        """AdaptiveRouter's warm-phase logic must no longer touch _stats directly."""
        from replicate_mcp.qos import AdaptiveRouter  # noqa: PLC0415

        router = AdaptiveRouter(explore_threshold=0)  # skip UCB1 phase
        router.register_model("a/m", initial_cost=0.001, initial_latency_ms=500)
        router.register_model("b/m", initial_cost=0.002, initial_latency_ms=400)

        # Record outcomes so ts_alpha/ts_beta diverge
        for _ in range(3):
            router.record_outcome("a/m", latency_ms=500, cost_usd=0.001, success=True)

        # This should not raise AttributeError and should return a valid model
        chosen = router.select_model(["a/m", "b/m"])
        assert chosen in ("a/m", "b/m")


# ---------------------------------------------------------------------------
# RoutingDecision dataclass
# ---------------------------------------------------------------------------


class TestRoutingDecision:
    def test_attributes(self) -> None:
        rd = RoutingDecision(
            selected_model="a/model",
            strategy="score",
            scores={"a/model": 0.01, "b/model": 0.05},
        )
        assert rd.selected_model == "a/model"
        assert rd.strategy == "score"
        assert rd.scores["a/model"] == pytest.approx(0.01)
        assert rd.scores["b/model"] == pytest.approx(0.05)

    def test_repr(self) -> None:
        rd = RoutingDecision(
            selected_model="a/model",
            strategy="thompson",
            scores={"a/model": 0.9},
        )
        r = repr(rd)
        assert "RoutingDecision" in r
        assert "a/model" in r
        assert "thompson" in r
        assert "0.9" in r

    def test_repr_with_multiple_scores(self) -> None:
        rd = RoutingDecision(
            selected_model="x/m",
            strategy="score",
            scores={"x/m": 0.1, "y/m": 0.5, "z/m": 0.8},
        )
        r = repr(rd)
        assert "RoutingDecision" in r
        assert "x/m" in r


# ---------------------------------------------------------------------------
# CostAwareRouter.select_model_explain() — score strategy
# ---------------------------------------------------------------------------


class TestSelectModelExplainScore:
    def _make_router(self) -> CostAwareRouter:
        return CostAwareRouter(
            strategy="score",
            weights=RoutingWeights(cost=1.0, latency=0.0, quality=0.0),
        )

    def test_empty_candidates_raises(self) -> None:
        r = self._make_router()
        with pytest.raises(ValueError, match="empty"):
            r.select_model_explain([])

    def test_single_candidate_returns_decision(self) -> None:
        r = self._make_router()
        rd = r.select_model_explain(["only/model"])
        assert isinstance(rd, RoutingDecision)
        assert rd.selected_model == "only/model"
        assert rd.strategy == "score"
        assert rd.scores == {"only/model": 0.0}

    def test_cheaper_model_selected_with_scores(self) -> None:
        r = self._make_router()
        r.register_model("expensive/model", initial_cost=0.10)
        r.register_model("cheap/model", initial_cost=0.001)
        rd = r.select_model_explain(["expensive/model", "cheap/model"])
        assert rd.selected_model == "cheap/model"
        assert rd.strategy == "score"
        # Score is cost-weighted — cheap should have a lower score
        assert rd.scores["cheap/model"] < rd.scores["expensive/model"]

    def test_scores_include_all_candidates(self) -> None:
        r = self._make_router()
        r.register_model("a/m", initial_cost=0.01)
        r.register_model("b/m", initial_cost=0.02)
        r.register_model("c/m", initial_cost=0.03)
        rd = r.select_model_explain(["a/m", "b/m", "c/m"])
        assert len(rd.scores) == 3
        assert "a/m" in rd.scores
        assert "b/m" in rd.scores
        assert "c/m" in rd.scores

    def test_auto_registers_unknown_models(self) -> None:
        r = self._make_router()
        rd = r.select_model_explain(["new/a", "new/b"])
        assert rd.selected_model in ("new/a", "new/b")
        assert len(rd.scores) == 2

    def test_latency_and_quality_weighted(self) -> None:
        """With balanced weights, lower-latency + higher-quality model wins."""
        r = CostAwareRouter(
            strategy="score",
            weights=RoutingWeights(cost=0.0, latency=0.5, quality=0.5),
        )
        r.register_model("fast/good", initial_latency_ms=500, initial_quality=0.95)
        r.register_model("slow/bad", initial_latency_ms=5000, initial_quality=0.5)
        rd = r.select_model_explain(["fast/good", "slow/bad"])
        assert rd.selected_model == "fast/good"
        assert rd.scores["fast/good"] < rd.scores["slow/bad"]

    def test_zero_total_weight_uses_denominator_guard(self) -> None:
        """When all weights are 0, total_w falls back to 1.0."""
        r = CostAwareRouter(
            strategy="score",
            weights=RoutingWeights(cost=0.0, latency=0.0, quality=0.0),
        )
        r.register_model("x/m")
        r.register_model("y/m")
        rd = r.select_model_explain(["x/m", "y/m"])
        assert rd.selected_model in ("x/m", "y/m")
        # Verify no inf/nan from divide-by-zero
        assert all(math.isfinite(v) for v in rd.scores.values())


# ---------------------------------------------------------------------------
# CostAwareRouter.select_model_explain() — thompson strategy
# ---------------------------------------------------------------------------


class TestSelectModelExplainThompson:
    def _make_router(self) -> CostAwareRouter:
        return CostAwareRouter(strategy="thompson")

    def test_empty_candidates_raises(self) -> None:
        r = self._make_router()
        with pytest.raises(ValueError, match="empty"):
            r.select_model_explain([])

    def test_single_candidate_returns_decision(self) -> None:
        r = self._make_router()
        rd = r.select_model_explain(["only/model"])
        assert isinstance(rd, RoutingDecision)
        assert rd.selected_model == "only/model"
        assert rd.strategy == "thompson"
        assert rd.scores == {"only/model": 0.0}

    def test_returns_routing_decision_with_scores(self) -> None:
        r = self._make_router()
        rd = r.select_model_explain(["a/model", "b/model"])
        assert isinstance(rd, RoutingDecision)
        assert rd.selected_model in ("a/model", "b/model")
        assert rd.strategy == "thompson"
        assert len(rd.scores) == 2
        # Thompson samples are in [0, 1]
        for score in rd.scores.values():
            assert 0.0 <= score <= 1.0

    def test_selected_model_has_highest_sample(self) -> None:
        """The model with the highest Thompson sample should be selected."""
        r = self._make_router()
        rd = r.select_model_explain(["a/m", "b/m", "c/m"])
        best_score = max(rd.scores.values())
        assert rd.scores[rd.selected_model] == pytest.approx(best_score)

    def test_thompson_favours_high_success_model(self) -> None:
        """After many successes, the good model should win most explain calls."""
        r = self._make_router()
        r.register_model("good/model")
        r.register_model("bad/model")
        for _ in range(50):
            r.record_outcome("good/model", latency_ms=100, cost_usd=0.01, success=True)
        for _ in range(50):
            r.record_outcome("bad/model", latency_ms=100, cost_usd=0.01, success=False)

        wins = sum(
            1
            for _ in range(200)
            if r.select_model_explain(["good/model", "bad/model"]).selected_model == "good/model"
        )
        assert wins > 140

    def test_auto_registers_unknown_models(self) -> None:
        r = self._make_router()
        rd = r.select_model_explain(["unseen/a", "unseen/b"])
        assert rd.selected_model in ("unseen/a", "unseen/b")
        assert len(rd.scores) == 2


# ---------------------------------------------------------------------------
# dump_state / load_state — persistence helpers
# ---------------------------------------------------------------------------


class TestDumpState:
    """Cover CostAwareRouter.dump_state() — serialises all per-model stats."""

    def test_dump_state_returns_dict(self) -> None:
        r = CostAwareRouter(strategy="score")
        r.register_model("a/model", initial_cost=0.01, initial_latency_ms=1000)
        r.register_model("b/model", initial_cost=0.02, initial_latency_ms=2000)
        state = r.dump_state()
        assert isinstance(state, dict)
        assert set(state.keys()) == {"a/model", "b/model"}

    def test_dump_state_values_are_dicts(self) -> None:
        r = CostAwareRouter()
        r.register_model("x/m", initial_cost=0.005)
        state = r.dump_state()
        raw = state["x/m"]
        assert isinstance(raw, dict)
        assert raw["model"] == "x/m"
        assert "ema_cost_usd" in raw
        assert "ema_latency_ms" in raw
        assert "ts_alpha" in raw

    def test_dump_state_empty_router(self) -> None:
        r = CostAwareRouter()
        state = r.dump_state()
        assert state == {}

    def test_dump_state_reflects_recorded_outcomes(self) -> None:
        r = CostAwareRouter()
        r.register_model("m1", initial_cost=0.01)
        r.record_outcome("m1", latency_ms=500, cost_usd=0.001, success=True)
        state = r.dump_state()
        assert state["m1"]["invocation_count"] == 1
        assert state["m1"]["success_count"] == 1


class TestLoadState:
    """Cover CostAwareRouter.load_state() — restores stats from a dict."""

    def test_load_state_restores_models(self) -> None:
        r1 = CostAwareRouter()
        r1.register_model("a/m", initial_cost=0.01)
        r1.record_outcome("a/m", latency_ms=500, cost_usd=0.001, success=True)
        state = r1.dump_state()

        r2 = CostAwareRouter()
        r2.load_state(state)
        stats = r2.stats()["a/m"]
        assert stats.invocation_count == 1
        assert stats.ema_cost_usd < 0.01

    def test_load_state_overwrites_existing(self) -> None:
        r = CostAwareRouter()
        r.register_model("x/m", initial_cost=0.10)
        # Manually build state with different cost
        from replicate_mcp.utils.router_state import serialise_stats  # noqa: PLC0415

        alt_stats = ModelStats(model="x/m", ema_cost_usd=0.001)
        r.load_state({"x/m": serialise_stats(alt_stats)})
        assert r.stats()["x/m"].ema_cost_usd == pytest.approx(0.001)

    def test_load_state_preserves_models_not_in_data(self) -> None:
        r = CostAwareRouter()
        r.register_model("keep/m", initial_cost=0.05)
        r.load_state({})  # empty data
        assert "keep/m" in r.stats()

    def test_load_state_invalid_entry_logged_and_skipped(self) -> None:
        """Entries that raise TypeError/KeyError during deserialise are skipped."""
        r = CostAwareRouter()
        # Pass a dict with a missing required field ("model")
        r.load_state({"bad/m": {"ema_cost_usd": 0.01}})
        # The bad entry should NOT appear in stats (deserialise_stats
        # raises TypeError because model is missing)
        assert "bad/m" not in r.stats()

    def test_load_state_unknown_keys_ignored(self) -> None:
        """Forward-compatible: unknown keys in the raw dict are silently dropped."""
        from replicate_mcp.utils.router_state import serialise_stats  # noqa: PLC0415

        r = CostAwareRouter()
        raw = serialise_stats(ModelStats(model="f/m", ema_cost_usd=0.01))
        raw["future_field"] = 42  # unknown key
        r.load_state({"f/m": raw})
        assert "f/m" in r.stats()
        assert r.stats()["f/m"].ema_cost_usd == pytest.approx(0.01)

    def test_round_trip_dump_load(self) -> None:
        """Full round-trip: dump → load preserves all stats."""
        r1 = CostAwareRouter(strategy="thompson")
        r1.register_model("m1", initial_cost=0.01, initial_latency_ms=1000)
        r1.register_model("m2", initial_cost=0.02, initial_latency_ms=2000)
        for _ in range(5):
            r1.record_outcome("m1", latency_ms=800, cost_usd=0.008, success=True)
        for _ in range(3):
            r1.record_outcome("m2", latency_ms=3000, cost_usd=0.025, success=False)

        state = r1.dump_state()

        r2 = CostAwareRouter(strategy="thompson")
        r2.load_state(state)

        s1_orig = r1.stats()
        s2_rest = r2.stats()
        for model in ("m1", "m2"):
            orig = s1_orig[model]
            rest = s2_rest[model]
            assert rest.invocation_count == orig.invocation_count
            assert rest.ema_cost_usd == pytest.approx(orig.ema_cost_usd)
            assert rest.ema_latency_ms == pytest.approx(orig.ema_latency_ms)
            assert rest.ts_alpha == pytest.approx(orig.ts_alpha)
            assert rest.ts_beta == pytest.approx(orig.ts_beta)
