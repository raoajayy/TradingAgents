"""SO-C3 — htf_momentum_v2: HTF alignment as a position-size scaler.

htf_momentum_v1 hard-vetoes trades against the higher-timeframe trend and earned
zero presets. v2 keeps every ROC entry but scales risk_pct by HTF conviction.
These tests lock the scaler (no-HTF neutral; aligned→max, opposed→min) and that
the strategy is registered + engine-runnable.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace as NS

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import (
    AssetClass,
    OHLCVBar,
    ProConfig,
    Timeframe,
    TradingMode,
)
from tradingagents.pro.backtest import BarReplay, SimBroker, build_strategy, is_registered
from tradingagents.pro.backtest.engine import BacktestEngine
from tradingagents.pro.backtest.registry import list_strategies, strategy_param_space

CONFIG = ProConfig(asset=AssetClass.BITCOIN, mode=TradingMode.BACKTEST,
                   max_debate_rounds=1)


def _htf(closes):
    return {Timeframe.D1: NS(bars=[NS(close=c) for c in closes])}


class TestRegistration:
    def test_registered(self):
        assert is_registered("htf_momentum_v2")
        assert "htf_momentum_v2" in [s.id for s in list_strategies()]
        r = strategy_param_space("htf_momentum_v2").resolve({})
        assert r["htf_scale_min"] == 0.25 and r["htf_scale_max"] == 1.5


class TestScaler:
    def _s(self, **ov):
        return build_strategy("htf_momentum_v2", ov or None)

    def test_no_htf_is_neutral(self):
        assert self._s()._htf_scale(NS(htf={}), "BUY") == 1.0

    def test_aligned_scales_up_opposed_scales_down(self):
        s = self._s()
        up = NS(htf=_htf([100, 101, 102, 103, 110]))    # last >> mean → HTF up
        assert s._htf_scale(up, "BUY") == 1.5           # aligned → max
        assert s._htf_scale(up, "SELL") == 0.25         # opposed → min

    def test_bounds_respected(self):
        s = self._s(htf_scale_min=0.5, htf_scale_max=2.0)
        up = NS(htf=_htf([100, 100, 100, 100, 130]))
        v = s._htf_scale(up, "BUY")
        assert 0.5 <= v <= 2.0


class TestEngineRun:
    def _bars(self, n=140, drift=3.0):
        bars, price = [], 1000.0
        for i in range(n):
            price += drift
            bars.append(OHLCVBar(timeframe=Timeframe.D1, start=BASE_TS + timedelta(days=i),
                                 open=price, high=price + 0.5, low=price - 0.5,
                                 close=price, volume=1e6))
        return bars

    def test_runs_and_trades_without_htf_context(self):
        # with no coarser HTF configured, v2 trades like plain momentum (scale=1)
        s = build_strategy("htf_momentum_v2", {"roc_threshold": 1.0})
        res = BacktestEngine(
            None, CONFIG,
            BarReplay("BTC-USD", AssetClass.BITCOIN, self._bars(), window=40,
                      precompute_indicators=True),
            broker=SimBroker(initial_equity=100_000.0), min_history=40,
            strategy=s).run()
        assert any(t.side == "BUY" for t in res.trades)
