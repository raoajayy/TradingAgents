"""Native-path risk circuit breaker (F3 / best-practice #7): once too many
losing trades stack up, new native entries are halted. Off by default."""

from datetime import timedelta

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
    BracketIntent,
    OrderIntent,
    ParamSpace,
    SimBroker,
)

CONFIG = ProConfig(asset=AssetClass.GOLD, mode=TradingMode.BACKTEST,
                   max_debate_rounds=1)


def _down_bars(n=40):
    bars, price = [], 1000.0
    for i in range(n):
        price -= 8.0  # steady downtrend → a long entered next bar stops out
        bars.append(OHLCVBar(
            timeframe=Timeframe.D1, start=BASE_TS + timedelta(days=i),
            open=price, high=price + 1.0, low=price - 3.0, close=price - 1.0,
            volume=1_000_000.0))
    return bars


class _AlwaysLong:
    """Re-enters a long whenever flat, with a tight protective stop — a
    guaranteed loser on a downtrend, so losses stack up fast."""

    id = "always_long"
    params = ParamSpace()

    def on_start(self, ctx):
        pass

    def on_bar(self, ctx):
        if ctx.positions:
            return []
        px = ctx.snapshot.bars[-1].close
        return [OrderIntent(kind="market", side="BUY", risk_pct=1.0,
                            bracket=BracketIntent(stop_loss=px * 0.98))]

    def on_fill(self, fill):
        pass

    def on_stop(self, ctx):
        pass


def _run(risk_breaker: bool):
    return BacktestEngine(
        None, CONFIG,
        BarReplay("XAUUSD", AssetClass.GOLD, _down_bars(), window=5),
        broker=SimBroker(initial_equity=100_000.0, max_gross_exposure_pct=100.0),
        memory=None, min_history=5, decide_every=1, strategy=_AlwaysLong(),
        risk_breaker=risk_breaker).run()


def test_breaker_halts_entries_after_consecutive_losses():
    off = _run(risk_breaker=False)
    on = _run(risk_breaker=True)
    # off: no breaker rejections; on: the circuit breaker fires and curbs entries
    assert "circuit_breaker" not in off.rejections
    assert on.rejections.get("circuit_breaker", 0) > 0
    assert on.executed < off.executed


def test_breaker_off_by_default_is_inert():
    # default construction (no risk_breaker arg) equals risk_breaker=False
    default = BacktestEngine(
        None, CONFIG,
        BarReplay("XAUUSD", AssetClass.GOLD, _down_bars(), window=5),
        broker=SimBroker(initial_equity=100_000.0, max_gross_exposure_pct=100.0),
        memory=None, min_history=5, decide_every=1, strategy=_AlwaysLong()).run()
    assert "circuit_breaker" not in default.rejections
