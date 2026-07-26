"""Dual moving-average crossover strategy (``ma_crossover_v1``) — the canonical
trend-change signal. A native order-book strategy computed from the
look-ahead-safe snapshot window (bars <= current)."""

from __future__ import annotations

from typing import Any

from tradingagents.pro.backtest.registry import register
from tradingagents.pro.backtest.strategies._exits import (
    exit_param_specs,
    trailing_fields,
)
from tradingagents.pro.backtest.strategy import (
    BracketIntent,
    OrderIntent,
    Param,
    ParamSpace,
    StrategyContext,
)

MA_CROSSOVER_V1_PARAMS = ParamSpace(
    Param("fast_period", "int", 5, 50, default=10),
    Param("slow_period", "int", 20, 200, default=30),
    Param("stop_atr_mult", "float", 1.5, 4.0, step=0.5, default=2.0),
    Param("trail_pct", "float", 0.01, 0.10, step=0.01, default=0.05),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
    *exit_param_specs(),
)


class MaCrossoverV1:
    """Dual moving-average crossover — enter on the bar a fast SMA crosses
    the slow SMA (golden cross → long, death cross → short) and ride it with a
    percentage trailing stop off an ATR initial stop. The entry SIGNAL (a
    cross event) is distinct from the channel breakout, the σ-fade, and the
    ROC threshold — the canonical trend-change archetype. Computed from the
    look-ahead-safe snapshot window."""

    def __init__(self, params: dict[str, Any]):
        self.id = "ma_crossover_v1"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        bars = ctx.snapshot.bars
        f, s = int(self.params["fast_period"]), int(self.params["slow_period"])
        if f >= s or len(bars) < s + 2:
            return []
        closes = [b.close for b in bars]
        fast_now = sum(closes[-f:]) / f
        slow_now = sum(closes[-s:]) / s
        fast_prev = sum(closes[-(f + 1):-1]) / f
        slow_prev = sum(closes[-(s + 1):-1]) / s
        atr = self._atr(bars, s)
        if atr <= 0:
            return []
        last = bars[-1]
        mult = float(self.params["stop_atr_mult"])
        trailing = trailing_fields(self.params)
        risk = float(self.params["risk_pct"])
        open_sides = {p.side for p in ctx.positions}

        cross_up = fast_prev <= slow_prev and fast_now > slow_now
        cross_down = fast_prev >= slow_prev and fast_now < slow_now
        if cross_up and "BUY" not in open_sides:
            return [self._entry("BUY", last.close - mult * atr,
                                last.close + 20 * atr, trailing, risk)]
        if (self.params["allow_short"] == "yes"
                and cross_down and "SELL" not in open_sides):
            return [self._entry("SELL", last.close + mult * atr,
                                last.close - 20 * atr, trailing, risk)]
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
    def _entry(side, stop, target, trailing, risk) -> OrderIntent:
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),),
                                  **trailing),
            tag=f"xover_{side.lower()}")


@register("ma_crossover_v1", MA_CROSSOVER_V1_PARAMS,
          description="Dual moving-average crossover — enter on a fast/slow SMA "
                      "cross (golden → long, death → short) and ride it with a "
                      "percentage trailing stop off an ATR initial stop. A "
                      "native order-book strategy; the canonical trend-change "
                      "signal. No model calls.")
def _build_ma_crossover_v1(params: dict[str, Any]) -> MaCrossoverV1:
    return MaCrossoverV1(params)


__all__ = ["MA_CROSSOVER_V1_PARAMS", "MaCrossoverV1"]
