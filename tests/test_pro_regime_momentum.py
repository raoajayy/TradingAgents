"""regime_momentum_v1 — ROC momentum gated by a market-health/regime read
(F2 / research best-practice #5). The gate stands aside in risk-off (crisis /
high-volatility) regimes; turning it off restores plain momentum."""

from datetime import timedelta

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import (
    AssetClass,
    MarketRegime,
    OHLCVBar,
    ProConfig,
    Timeframe,
    TradingMode,
)
from tradingagents.pro.analytics.features import classify_regime
from tradingagents.pro.backtest import (
    BacktestEngine,
    BarReplay,
    SimBroker,
    build_strategy,
    list_strategies,
)

CONFIG = ProConfig(asset=AssetClass.GOLD, mode=TradingMode.BACKTEST,
                   max_debate_rounds=1)


def _volatile_up(n=60):
    """Net-upward drift with big alternating swings → high realized volatility
    AND positive ROC, so momentum would fire but the regime gate should not."""
    bars, base = [], 1000.0
    for i in range(n):
        base *= 1.04
        px = base * (1.10 if i % 2 else 0.90)  # ±10% noise on top of the drift
        bars.append(OHLCVBar(
            timeframe=Timeframe.D1, start=BASE_TS + timedelta(days=i),
            open=px, high=px * 1.02, low=px * 0.98, close=px, volume=1_000_000.0))
    return bars


def test_registered_with_gate_param():
    info = next((s for s in list_strategies() if s.id == "regime_momentum_v1"), None)
    assert info is not None
    assert "regime_gate" in [p["name"] for p in info.params]


def test_precondition_series_is_risk_off():
    # the fixture must actually classify as a risk-off regime for the gate to bite
    assert classify_regime(_volatile_up()) in (
        MarketRegime.CRISIS, MarketRegime.HIGH_VOLATILITY)


def _run(gate: str):
    return BacktestEngine(
        None, CONFIG,
        BarReplay("XAUUSD", AssetClass.GOLD, _volatile_up(), window=16),
        broker=SimBroker(initial_equity=1_000_000.0, max_gross_exposure_pct=100.0),
        memory=None, min_history=16, decide_every=1,
        strategy=build_strategy("regime_momentum_v1",
                                {"regime_gate": gate, "roc_threshold": 2.0})).run()


def test_gate_suppresses_entries_in_risk_off_regime():
    gated = _run("on")
    ungated = _run("off")
    # gate on → stands aside in the high-vol regime; gate off → momentum trades
    assert gated.executed == 0
    assert ungated.executed > 0
