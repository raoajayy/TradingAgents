"""momentum_v2 — volatility-relative (z-score) momentum. Registered +
engine-runnable; its trigger self-scales, so it stays active on small-move
series where momentum_v1's absolute ROC threshold goes inert (the Strategy Lab
gap fix, docs/backtests/strategy_lab/01_gap_analysis.md)."""

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
from tradingagents.pro.backtest import BarReplay, build_strategy, is_registered
from tradingagents.pro.backtest.engine import BacktestEngine
from tradingagents.pro.backtest.registry import list_strategies, strategy_param_space

CONFIG = ProConfig(asset=AssetClass.BITCOIN, mode=TradingMode.BACKTEST,
                   max_debate_rounds=1)


def _small_moves_uptrend(n=200, p0=1000.0, drift_pct=0.0008, noise=0.0015):
    """Low-amplitude but persistent uptrend with realistic per-bar noise: mean
    move ~0.2%/bar (so a 14-bar cumulative ~2.8%, below momentum_v1's default 5%
    absolute ROC threshold), but the drift is strong relative to the small
    realized volatility → a high vol-relative z-score, so momentum_v2 trades.
    Deterministic (seeded)."""
    rng = random.Random(7)
    bars, price = [], p0
    for i in range(n):
        prev = price
        price = max(1.0, price * (1 + drift_pct + rng.uniform(-noise, noise)))
        hi = max(prev, price) * (1 + abs(rng.uniform(0, noise)))
        lo = min(prev, price) * (1 - abs(rng.uniform(0, noise)))
        bars.append(OHLCVBar(
            timeframe=Timeframe.M15, start=BASE_TS + timedelta(minutes=15 * i),
            open=prev, high=hi, low=lo, close=price, volume=1_000_000.0))
    return bars


def _strategy(sid, **overrides):
    return build_strategy(sid, overrides or None)


class TestRegistration:
    def test_registered_and_discoverable(self):
        assert is_registered("momentum_v2")
        assert "momentum_v2" in [s.id for s in list_strategies()]
        names = [p.name for p in strategy_param_space("momentum_v2")]
        assert "entry_sigma" in names and "roc_period" in names

    def test_defaults_resolve(self):
        r = strategy_param_space("momentum_v2").resolve({})
        assert r["entry_sigma"] == 2.0 and r["roc_period"] == 14


class TestEngineRun:
    def _run(self, sid, bars, **overrides):
        replay = BarReplay("BTC-USD", AssetClass.BITCOIN, bars, window=40,
                           precompute_indicators=True)
        return BacktestEngine(None, CONFIG, replay,
                              strategy=_strategy(sid, **overrides),
                              min_history=40).run()

    def test_trades_where_absolute_threshold_is_inert(self):
        """The gap fix: on a small-amplitude trend (cumulative moves well below
        5%), momentum_v1's absolute ROC threshold barely fires, while
        momentum_v2's vol-relative trigger stays active and trades materially
        more (and takes longs into the uptrend)."""
        bars = _small_moves_uptrend()
        v1 = self._run("momentum_v1", bars)              # default 5% threshold
        v2 = self._run("momentum_v2", bars, entry_sigma=1.5)
        assert len(v2.trades) >= 1 and any(t.side == "BUY" for t in v2.trades)
        assert len(v2.trades) > len(v1.trades), (
            f"v2 should out-trade the inert v1 (v2={len(v2.trades)}, v1={len(v1.trades)})")

    def test_higher_sigma_is_more_selective(self):
        """A higher entry_sigma bar demands a stronger vol-relative move, so it
        never trades more than a lower bar (monotonic selectivity)."""
        bars = _small_moves_uptrend()
        loose = self._run("momentum_v2", bars, entry_sigma=1.0)
        strict = self._run("momentum_v2", bars, entry_sigma=3.0)
        assert len(strict.trades) <= len(loose.trades)

    def test_deterministic(self):
        bars = _small_moves_uptrend()
        a = self._run("momentum_v2", bars, entry_sigma=1.5)
        b = self._run("momentum_v2", bars, entry_sigma=1.5)
        assert [(t.side, t.pnl) for t in a.trades] == [(t.side, t.pnl) for t in b.trades]
