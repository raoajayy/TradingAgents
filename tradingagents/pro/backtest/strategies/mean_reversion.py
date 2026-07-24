"""Mean-reversion strategy — Bollinger-style fade of a stretch beyond N standard
deviations from the SMA (``mean_reversion_v1``). The counter-trend package that
complements trend_following_v1; a native order-book strategy computed from the
look-ahead-safe snapshot window (bars <= current)."""

from __future__ import annotations

from typing import Any

from tradingagents.pro.backtest.registry import register
from tradingagents.pro.backtest.strategy import (
    BracketIntent,
    OrderIntent,
    Param,
    ParamSpace,
    StrategyContext,
)

MEAN_REVERSION_V1_PARAMS = ParamSpace(
    Param("lookback", "int", 10, 60, default=20),
    Param("entry_std", "float", 1.5, 3.0, step=0.5, default=2.0),
    Param("stop_atr_mult", "float", 1.5, 4.0, step=0.5, default=3.0),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
)


class MeanReversionV1:
    """Bollinger-style mean reversion — fade a stretch beyond ``entry_std``
    standard deviations from the SMA, target the mean, and stop on a further
    ATR move against the entry. The counter-trend package that recurs in the
    trader research (docs/research/02_pattern_report.md) — the natural
    complement to trend_following_v1, and a second real consumer of the native
    order-book path. Everything is computed from the look-ahead-safe snapshot
    window (bars <= current)."""

    def __init__(self, params: dict[str, Any]):
        self.id = "mean_reversion_v1"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        bars = ctx.snapshot.bars
        n = int(self.params["lookback"])
        if len(bars) < n + 2:
            return []
        closes = [b.close for b in bars[-n:]]
        sma = sum(closes) / n
        std = (sum((c - sma) ** 2 for c in closes) / n) ** 0.5
        atr = self._atr(bars, n)
        if std <= 0 or atr <= 0:
            return []
        last = bars[-1]
        k = float(self.params["entry_std"])
        lower, upper = sma - k * std, sma + k * std
        mult = float(self.params["stop_atr_mult"])
        risk = float(self.params["risk_pct"])
        open_sides = {p.side for p in ctx.positions}  # one position per side

        # stretched BELOW the band → fade up toward the mean (target = SMA)
        if last.close < lower and "BUY" not in open_sides:
            return [self._entry("BUY", last.close - mult * atr, sma, risk)]
        if (self.params["allow_short"] == "yes"
                and last.close > upper and "SELL" not in open_sides):
            return [self._entry("SELL", last.close + mult * atr, sma, risk)]
        return []

    def on_fill(self, fill) -> None: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...

    @staticmethod
    def _atr(bars, period: int) -> float:
        recent = bars[-(period + 1):]
        trs = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
               for p, c in zip(recent, recent[1:], strict=False)]
        return sum(trs) / len(trs) if trs else 0.0

    @staticmethod
    def _entry(side, stop, target, risk) -> OrderIntent:
        # enter market at the next bar's open; the single target is the mean
        # (no trailing — mean reversion exits at the mean or the stop)
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),)),
            tag=f"mr_{side.lower()}")


@register("mean_reversion_v1", MEAN_REVERSION_V1_PARAMS,
          description="Bollinger-style mean reversion — fade a move beyond N "
                      "standard deviations from the SMA, target the mean, ATR "
                      "stop (long & short). A native order-book strategy "
                      "implementing the research's counter-trend pattern. No "
                      "model calls.")
def _build_mean_reversion_v1(params: dict[str, Any]) -> MeanReversionV1:
    return MeanReversionV1(params)


__all__ = ["MEAN_REVERSION_V1_PARAMS", "MeanReversionV1"]
