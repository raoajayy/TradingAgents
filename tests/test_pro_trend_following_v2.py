"""trend_following_v2 — Donchian breakout that pyramids on winners (F1 /
research best-practice #3). Registration, the pyramid on_bar signal, and an
engine run that stacks multiple same-direction units in an uptrend."""

from datetime import timedelta
from types import SimpleNamespace

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import (
    AssetClass,
    OHLCVBar,
    ProConfig,
    Timeframe,
    TradingMode,
)
from tradingagents.pro.backtest import (
    BacktestEngine,
    BarReplay,
    SimBroker,
    build_strategy,
    list_strategies,
)

CONFIG = ProConfig(asset=AssetClass.GOLD, mode=TradingMode.BACKTEST,
                   max_debate_rounds=1)


def _uptrend(n=80):
    bars, price = [], 1000.0
    for i in range(n):
        price += 2.0  # steady rise so the channel breaks and adds trigger
        bars.append(OHLCVBar(
            timeframe=Timeframe.D1, start=BASE_TS + timedelta(days=i),
            open=price, high=price + 0.5, low=price - 0.5, close=price,
            volume=1_000_000.0))
    return bars


def test_registered_with_pyramiding_params():
    info = next((s for s in list_strategies() if s.id == "trend_following_v2"), None)
    assert info is not None
    names = [p["name"] for p in info.params]
    assert "max_adds" in names and "add_atr_mult" in names


def test_on_bar_pyramids_a_winning_long():
    strat = build_strategy("trend_following_v2",
                           {"donchian_period": 10, "add_atr_mult": 1.0,
                            "max_adds": 2})
    bars = _uptrend(40)
    # an open long entered well below the current close → price has advanced,
    # so on_bar should emit an ADD (not a fresh breakout)
    long_pos = SimpleNamespace(side="BUY", entry_price=bars[-1].close - 20.0)
    ctx = SimpleNamespace(snapshot=SimpleNamespace(bars=bars),
                          positions=(long_pos,), equity=100_000.0, params={})
    intents = strat.on_bar(ctx)
    assert len(intents) == 1 and intents[0].tag == "tf2_add_buy"

    # once max_adds+1 units are already open, no further add
    ctx.positions = (long_pos, long_pos, long_pos)
    assert strat.on_bar(ctx) == []


def _run(max_adds: int):
    return BacktestEngine(
        None, CONFIG,
        BarReplay("XAUUSD", AssetClass.GOLD, _uptrend(), window=25),
        broker=SimBroker(initial_equity=1_000_000.0, max_gross_exposure_pct=100.0,
                         max_open_positions=6, max_same_direction=6),
        memory=None, min_history=25, decide_every=1,
        strategy=build_strategy("trend_following_v2",
                                {"donchian_period": 10, "max_adds": max_adds,
                                 "add_atr_mult": 1.0})).run()


def test_engine_stacks_units_when_pyramiding():
    pyramided = _run(max_adds=3)
    single = _run(max_adds=0)
    # pyramiding opens strictly more same-direction fills than the no-add variant
    assert pyramided.executed > single.executed
    assert single.executed >= 1
