"""SO-C2 — opt-in ADX chop filter on ma_crossover_v1.

ma_crossover_v1 earned zero tuned presets (whipsaw in ranging markets). The
filter suppresses crosses when trend strength (ADX) is below adx_min. Off by
default → geometry unchanged; fail-closed on a missing ADX reading.
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

CONFIG = ProConfig(asset=AssetClass.BITCOIN, mode=TradingMode.BACKTEST,
                   max_debate_rounds=1)


def _bars(n=400, seed=5):
    random.seed(seed)
    bars, p = [], 1000.0
    for i in range(n):
        p = max(50.0, p + (4 if (i // 50) % 2 == 0 else -3) + random.uniform(-9, 9))
        o = p
        c = p + random.uniform(-5, 5)
        h = max(o, c) + abs(random.uniform(0, 9))
        low = max(0.1, min(o, c) - abs(random.uniform(0, 9)))
        bars.append(OHLCVBar(timeframe=Timeframe.H1, start=BASE_TS + timedelta(hours=i),
                             open=o, high=h, low=low, close=c, volume=1000.0))
    return bars


def _run(**ov):
    s = build_strategy("ma_crossover_v1", {"fast_period": 8, "slow_period": 25, **ov})
    return BacktestEngine(
        None, CONFIG,
        BarReplay("BTC-USD", AssetClass.BITCOIN, _bars(), window=40,
                  precompute_indicators=True),
        broker=SimBroker(initial_equity=100_000.0), min_history=40,
        strategy=s).run()


def test_defaults_off_and_params_present():
    r = strategy_param_space("ma_crossover_v1").resolve({})
    assert r["adx_filter"] == "off"
    assert r["adx_min"] == 20.0


def test_filter_off_matches_no_filter():
    # explicitly-off must equal the default (off) run — no behavior change
    a = [(t.side, t.pnl) for t in _run().trades]
    b = [(t.side, t.pnl) for t in _run(adx_filter="off").trades]
    assert a == b


def test_filter_on_is_more_selective():
    off = _run()
    on = _run(adx_filter="on", adx_min=25.0)
    assert len(on.trades) <= len(off.trades)


def test_higher_adx_min_no_more_trades_than_lower():
    lo = _run(adx_filter="on", adx_min=15.0)
    hi = _run(adx_filter="on", adx_min=30.0)
    assert len(hi.trades) <= len(lo.trades)
