"""Trend-following strategies — Donchian-channel breakout (``trend_following_v1``,
the research's #1 systematic pattern) and its pyramiding-on-winners variant
(``trend_following_v2``, best-practice #3). Native order-book strategies: they
emit OrderIntents from ``on_bar`` (executed through the broker's pending-order
book), so they need no LLM/pipeline. Everything is computed from the
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

TREND_V1_PARAMS = ParamSpace(
    Param("donchian_period", "int", 10, 100, default=20),
    Param("stop_atr_mult", "float", 1.5, 4.0, step=0.5, default=2.0),
    Param("trail_pct", "float", 0.01, 0.10, step=0.01, default=0.05),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
)


class TrendFollowingV1:
    """Donchian-channel breakout with a volatility (ATR) stop and a percentage
    trailing exit — a runnable reference implementation of the systematic
    trend-following package that recurred most strongly in the trader research
    (docs/research/02_pattern_report.md: channel breakout + ATR stop, lift 10.6;
    trend-following + vol-normalized sizing, the highest-support pattern).

    A NATIVE strategy: it emits OrderIntents from on_bar (executed through the
    broker's pending-order book), so it needs no LLM/pipeline. Everything is
    computed from the look-ahead-safe snapshot window (bars <= current)."""

    def __init__(self, params: dict[str, Any]):
        self.id = "trend_following_v1"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        bars = ctx.snapshot.bars
        period = int(self.params["donchian_period"])
        if len(bars) < period + 2:
            return []
        atr = self._atr(bars, period)
        if atr <= 0:
            return []
        last = bars[-1]
        prior = bars[-(period + 1):-1]  # the N bars BEFORE the current one
        prior_high = max(b.high for b in prior)
        prior_low = min(b.low for b in prior)
        open_sides = {p.side for p in ctx.positions}  # one position per side
        mult = float(self.params["stop_atr_mult"])
        trail = float(self.params["trail_pct"])
        risk = float(self.params["risk_pct"])

        if last.close > prior_high and "BUY" not in open_sides:
            return [self._entry("BUY", last.close, last.close - mult * atr,
                                last.close + 20 * atr, trail, risk)]
        if (self.params["allow_short"] == "yes"
                and last.close < prior_low and "SELL" not in open_sides):
            return [self._entry("SELL", last.close, last.close + mult * atr,
                                last.close - 20 * atr, trail, risk)]
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
    def _entry(side, ref, stop, target, trail, risk) -> OrderIntent:
        # confirm the breakout on this bar's close, enter market at the next
        # bar's open; ride the trailing stop (far TP so trailing is the exit)
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),),
                                  trailing="pct", trailing_mult=trail),
            tag=f"tf_{side.lower()}")


@register("trend_following_v1", TREND_V1_PARAMS,
          description="Donchian-channel breakout with an ATR stop and a "
                      "percentage trailing exit — a native order-book strategy "
                      "(long & short). Reference implementation of the research's "
                      "top systematic trend-following pattern. No model calls.")
def _build_trend_following_v1(params: dict[str, Any]) -> TrendFollowingV1:
    return TrendFollowingV1(params)


# --- trend_following_v2: pyramiding on winners (best-practice #3) -------------

TREND_V2_PARAMS = ParamSpace(
    Param("donchian_period", "int", 10, 100, default=20),
    Param("stop_atr_mult", "float", 1.5, 4.0, step=0.5, default=2.0),
    Param("trail_pct", "float", 0.01, 0.10, step=0.01, default=0.05),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
    Param("max_adds", "int", 0, 4, default=2),
    Param("add_atr_mult", "float", 0.5, 3.0, step=0.5, default=1.0),
)


class TrendFollowingV2:
    """trend_following_v1 + PYRAMIDING ON WINNERS (research best-practice #3:
    winners pyramided, losers never averaged). After the Donchian breakout
    entry, it adds same-direction units as price advances ``add_atr_mult``×ATR
    beyond the most recent add, up to ``max_adds`` — each unit carrying its own
    ATR stop + % trailing, so the aggregate stop ratchets up with price. Adds
    require the position to be in profit (price beyond the last entry), so
    losers are never averaged down. Pyramid depth is also bound by the broker's
    ``max_same_direction`` cap. ``max_adds=0`` reduces to trend_following_v1."""

    def __init__(self, params: dict[str, Any]):
        self.id = "trend_following_v2"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        bars = ctx.snapshot.bars
        period = int(self.params["donchian_period"])
        if len(bars) < period + 2:
            return []
        atr = TrendFollowingV1._atr(bars, period)
        if atr <= 0:
            return []
        last = bars[-1]
        prior = bars[-(period + 1):-1]
        prior_high = max(b.high for b in prior)
        prior_low = min(b.low for b in prior)
        mult = float(self.params["stop_atr_mult"])
        trail = float(self.params["trail_pct"])
        risk = float(self.params["risk_pct"])
        max_adds = int(self.params["max_adds"])
        add_m = float(self.params["add_atr_mult"])
        longs = [p for p in ctx.positions if p.side == "BUY"]
        shorts = [p for p in ctx.positions if p.side == "SELL"]

        # initial breakout entry (identical geometry to trend_following_v1)
        if last.close > prior_high and not longs:
            return [self._unit("BUY", last.close, mult, atr, trail, risk, add=False)]
        if (self.params["allow_short"] == "yes"
                and last.close < prior_low and not shorts):
            return [self._unit("SELL", last.close, mult, atr, trail, risk, add=False)]
        # pyramid: add to a WINNING position as price advances past the last add
        if (longs and len(longs) < max_adds + 1
                and last.close >= max(p.entry_price for p in longs) + add_m * atr):
            return [self._unit("BUY", last.close, mult, atr, trail, risk, add=True)]
        if (shorts and self.params["allow_short"] == "yes"
                and len(shorts) < max_adds + 1
                and last.close <= min(p.entry_price for p in shorts) - add_m * atr):
            return [self._unit("SELL", last.close, mult, atr, trail, risk, add=True)]
        return []

    def on_fill(self, fill) -> None: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...

    @staticmethod
    def _unit(side, ref, mult, atr, trail, risk, *, add) -> OrderIntent:
        stop = ref - mult * atr if side == "BUY" else ref + mult * atr
        target = ref + 20 * atr if side == "BUY" else ref - 20 * atr
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),),
                                  trailing="pct", trailing_mult=trail),
            tag=f"tf2_{'add' if add else 'entry'}_{side.lower()}")


@register("trend_following_v2", TREND_V2_PARAMS,
          description="Donchian breakout that PYRAMIDS on winners — adds "
                      "same-direction units as price advances (ATR-spaced), each "
                      "with its own ATR stop + % trailing; losers never averaged. "
                      "Native, long & short. No model calls.")
def _build_trend_following_v2(params: dict[str, Any]) -> TrendFollowingV2:
    return TrendFollowingV2(params)


__all__ = [
    "TREND_V1_PARAMS",
    "TREND_V2_PARAMS",
    "TrendFollowingV1",
    "TrendFollowingV2",
]
