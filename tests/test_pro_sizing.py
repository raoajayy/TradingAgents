"""SO-C4 (EXPERIMENTAL) — Kelly-capped sizing + equity-curve filter helpers,
and their off-by-default wiring on trend_following_v1."""

from __future__ import annotations

from tradingagents.pro.backtest.registry import strategy_param_space
from tradingagents.pro.backtest.strategies._sizing import (
    KELLY_FLOOR,
    KELLY_MIN_TRADES,
    equity_below_ma,
    kelly_risk,
)


class TestKellyRisk:
    def test_below_min_trades_returns_base(self):
        assert kelly_risk([1.0, -1.0] * 3, 1.0) == 1.0  # < KELLY_MIN_TRADES

    def test_negative_edge_floors(self):
        # mostly losers → Kelly 0 → floor × base
        pnls = [-1.0] * (KELLY_MIN_TRADES - 2) + [0.5, 0.5]
        assert kelly_risk(pnls, 2.0) == 2.0 * KELLY_FLOOR

    def test_strong_edge_scales_toward_base_never_above(self):
        # all wins, big payoff → capped Kelly → risk in (floor·base, base]
        pnls = [3.0] * KELLY_MIN_TRADES
        r = kelly_risk(pnls, 1.0)
        assert 1.0 * KELLY_FLOOR <= r <= 1.0

    def test_never_exceeds_base(self):
        pnls = [5.0] * 20 + [-1.0] * 5
        assert kelly_risk(pnls, 1.5) <= 1.5


class TestEquityFilter:
    def test_allows_until_enough_history(self):
        assert equity_below_ma([100.0, 101.0], period=20) is False

    def test_true_when_below_ma(self):
        hist = [100.0] * 20 + [90.0]
        assert equity_below_ma(hist, period=20) is True

    def test_false_when_above_ma(self):
        hist = [100.0] * 20 + [110.0]
        assert equity_below_ma(hist, period=20) is False


class TestParamsDefaultOff:
    def test_trend_following_v1_experimental_params_default_off(self):
        r = strategy_param_space("trend_following_v1").resolve({})
        assert r["equity_filter"] == "off"
        assert r["kelly_sizing"] == "off"
        assert r["equity_ma"] == 20
