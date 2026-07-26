"""SO-C1 — opt-in ATR / Chandelier trailing exits on the trailing strategies.

The broker already implements pct/atr/chandelier trailing; these tests lock the
additive, off-by-default wiring: default stays `pct` (equivalence untouched), and
atr/chandelier actually engage the broker's other modes.
"""

from __future__ import annotations

import random
from datetime import timedelta

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import (
    AssetClass,
    OHLCVBar,
    ProConfig,
    Timeframe,
    TradingMode,
)
from tradingagents.pro.backtest import BarReplay, SimBroker, build_strategy
from tradingagents.pro.backtest.engine import BacktestEngine
from tradingagents.pro.backtest.registry import strategy_param_space
from tradingagents.pro.backtest.strategies._exits import trailing_fields

CONFIG = ProConfig(asset=AssetClass.BITCOIN, mode=TradingMode.BACKTEST,
                   max_debate_rounds=1)
TRAILING_STRATEGIES = ("trend_following_v1", "trend_following_v2",
                       "ma_crossover_v1", "volatility_breakout_v1")


def _bars(n=300, seed=3):
    random.seed(seed)
    bars, p = [], 1000.0
    for i in range(n):
        p = max(50.0, p + (5 if (i // 40) % 2 == 0 else -3) + random.uniform(-6, 6))
        o = p
        c = p + random.uniform(-4, 4)
        h = max(o, c) + abs(random.uniform(0, 7))
        low = max(0.1, min(o, c) - abs(random.uniform(0, 7)))
        bars.append(OHLCVBar(timeframe=Timeframe.H1, start=BASE_TS + timedelta(hours=i),
                             open=o, high=h, low=low, close=c, volume=1000.0))
    return bars


class TestTrailingFields:
    def test_default_is_pct_backward_compatible(self):
        f = trailing_fields({"trail_pct": 0.05})
        assert f == {"trailing": "pct", "trailing_mult": 0.05}

    def test_atr_and_chandelier_use_atr_mult_and_period(self):
        for mode in ("atr", "chandelier"):
            f = trailing_fields({"trail_pct": 0.05, "trail_mode": mode,
                                 "trail_atr_mult": 3.0, "trail_period": 22})
            assert f == {"trailing": mode, "trailing_mult": 3.0, "trailing_period": 22}


class TestParamsAndBehavior:
    def test_all_trailing_strategies_expose_exit_params_defaulting_pct(self):
        for sid in TRAILING_STRATEGIES:
            r = strategy_param_space(sid).resolve({})
            assert r["trail_mode"] == "pct"
            assert "trail_atr_mult" in r and "trail_period" in r

    def _run(self, sid, mode):
        s = build_strategy(sid, {"trail_mode": mode, "trail_atr_mult": 3.0,
                                 "trail_period": 22})
        return BacktestEngine(
            None, CONFIG,
            BarReplay("BTC-USD", AssetClass.BITCOIN, _bars(), window=25,
                      precompute_indicators=True),
            broker=SimBroker(initial_equity=100_000.0), min_history=25,
            strategy=s).run()

    def test_atr_and_chandelier_execute_and_differ_from_pct(self):
        # trend_following_v1 trades on this synthetic trend; the three modes
        # must all run and not be identical (they anchor the stop differently)
        base = [(t.side, round(t.pnl, 4)) for t in self._run("trend_following_v1", "pct").trades]
        assert base, "pct baseline should trade"
        for mode in ("atr", "chandelier"):
            got = [(t.side, round(t.pnl, 4)) for t in self._run("trend_following_v1", mode).trades]
            assert got, f"{mode} should trade"
            assert got != base, f"{mode} should differ from pct"

    def test_deterministic(self):
        a = [(t.side, t.pnl) for t in self._run("volatility_breakout_v1", "chandelier").trades]
        b = [(t.side, t.pnl) for t in self._run("volatility_breakout_v1", "chandelier").trades]
        assert a == b
