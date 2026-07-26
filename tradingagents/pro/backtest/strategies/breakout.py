"""Volatility-breakout strategy (``volatility_breakout_v1``) — a Bollinger-squeeze
breakout: a volatility-regime archetype distinct from the price/trend
strategies. A native order-book strategy computed from the look-ahead-safe
snapshot window (bars <= current)."""

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

VOLATILITY_BREAKOUT_V1_PARAMS = ParamSpace(
    Param("lookback", "int", 10, 50, default=20),
    Param("squeeze_pct", "float", 0.01, 0.15, step=0.01, default=0.05),
    Param("stop_atr_mult", "float", 1.5, 4.0, step=0.5, default=2.0),
    Param("trail_pct", "float", 0.01, 0.10, step=0.01, default=0.05),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
    *exit_param_specs(),
)


class VolatilityBreakoutV1:
    """Bollinger-squeeze breakout — a VOLATILITY-REGIME archetype distinct from
    the five price/trend strategies. When band width (2σ / SMA) has contracted
    below ``squeeze_pct`` (a low-volatility coil), arm; then take the direction
    of the break OUT of the band as volatility expands, riding it with a
    percentage trailing stop. The classic 'volatility precedes price' setup
    from the research. Long & short, look-ahead-safe."""

    def __init__(self, params: dict[str, Any]):
        self.id = "volatility_breakout_v1"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        bars = ctx.snapshot.bars
        n = int(self.params["lookback"])
        if len(bars) < n + 3:
            return []
        closes = [b.close for b in bars]
        sma_now, std_now = self._stats(closes[-n:])
        sma_prev, std_prev = self._stats(closes[-(n + 1):-1])
        atr = self._atr(bars, n)
        if sma_now <= 0 or sma_prev <= 0 or std_now <= 0 or atr <= 0:
            return []
        width_prev = 2 * std_prev / sma_prev  # relative band width one bar ago
        squeezed = width_prev < float(self.params["squeeze_pct"])
        if not squeezed:
            return []
        upper, lower = sma_now + 2 * std_now, sma_now - 2 * std_now
        last = bars[-1]
        mult = float(self.params["stop_atr_mult"])
        trailing = trailing_fields(self.params)
        risk = float(self.params["risk_pct"])
        open_sides = {p.side for p in ctx.positions}

        if last.close > upper and "BUY" not in open_sides:
            return [self._entry("BUY", last.close - mult * atr,
                                last.close + 20 * atr, trailing, risk)]
        if (self.params["allow_short"] == "yes"
                and last.close < lower and "SELL" not in open_sides):
            return [self._entry("SELL", last.close + mult * atr,
                                last.close - 20 * atr, trailing, risk)]
        return []

    def on_fill(self, fill) -> None: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...

    @staticmethod
    def _stats(seq) -> tuple[float, float]:
        m = sum(seq) / len(seq)
        return m, (sum((x - m) ** 2 for x in seq) / len(seq)) ** 0.5

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
            tag=f"vol_{side.lower()}")


@register("volatility_breakout_v1", VOLATILITY_BREAKOUT_V1_PARAMS,
          description="Bollinger-squeeze breakout — after band width contracts "
                      "below a threshold (a low-volatility coil), take the "
                      "break out of the band as volatility expands, riding it "
                      "with a percentage trailing stop (long & short). A native "
                      "volatility-regime strategy. No model calls.")
def _build_volatility_breakout_v1(params: dict[str, Any]) -> VolatilityBreakoutV1:
    return VolatilityBreakoutV1(params)


__all__ = ["VOLATILITY_BREAKOUT_V1_PARAMS", "VolatilityBreakoutV1"]
