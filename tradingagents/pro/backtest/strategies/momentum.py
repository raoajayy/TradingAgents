"""Momentum strategies — plain rate-of-change (``momentum_v1``), higher-timeframe
-confirmed momentum (``htf_momentum_v1``, the first consumer of the multi-TF
context), and market-health/regime-gated momentum (``regime_momentum_v1``,
best-practice #5). All native order-book strategies computed from the
look-ahead-safe snapshot window (bars <= current)."""

from __future__ import annotations

from typing import Any

from tradingagents.contracts import MarketRegime, Timeframe
from tradingagents.pro.backtest.registry import register
from tradingagents.pro.backtest.strategy import (
    BracketIntent,
    OrderIntent,
    Param,
    ParamSpace,
    StrategyContext,
)

MOMENTUM_V1_PARAMS = ParamSpace(
    Param("roc_period", "int", 5, 40, default=14),
    Param("roc_threshold", "float", 1.0, 15.0, step=1.0, default=5.0),
    Param("stop_atr_mult", "float", 1.0, 4.0, step=0.5, default=2.0),
    Param("target_atr_mult", "float", 1.0, 6.0, step=0.5, default=3.0),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
)


class MomentumV1:
    """Rate-of-change momentum — enter in the direction of a strong recent move
    (|ROC| over ``roc_period`` beyond ``roc_threshold`` percent) and take a
    FIXED-R outcome (ATR stop + ATR target, no trailing). Distinct from the
    breakout (channel + trailing) and mean-reversion (fade the mean) packages;
    the momentum/relative-strength archetype from the research. Long & short,
    computed from the look-ahead-safe snapshot window."""

    def __init__(self, params: dict[str, Any]):
        self.id = "momentum_v1"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        bars = ctx.snapshot.bars
        p = int(self.params["roc_period"])
        if len(bars) < p + 2:
            return []
        atr = self._atr(bars, p)
        ref = bars[-(p + 1)]
        if atr <= 0 or ref.close <= 0:
            return []
        last = bars[-1]
        roc = (last.close / ref.close - 1.0) * 100.0
        thr = float(self.params["roc_threshold"])
        stop_m = float(self.params["stop_atr_mult"])
        tgt_m = float(self.params["target_atr_mult"])
        risk = float(self.params["risk_pct"])
        open_sides = {pos.side for pos in ctx.positions}

        if roc > thr and "BUY" not in open_sides:
            return [self._entry("BUY", last.close - stop_m * atr,
                                last.close + tgt_m * atr, risk)]
        if (self.params["allow_short"] == "yes"
                and roc < -thr and "SELL" not in open_sides):
            return [self._entry("SELL", last.close + stop_m * atr,
                                last.close - tgt_m * atr, risk)]
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
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),)),
            tag=f"mom_{side.lower()}")


@register("momentum_v1", MOMENTUM_V1_PARAMS,
          description="Rate-of-change momentum — enter with a strong recent "
                      "move (ROC beyond a threshold) for a fixed-R ATR "
                      "stop/target (long & short). A native order-book strategy "
                      "implementing the research's momentum/relative-strength "
                      "pattern. No model calls.")
def _build_momentum_v1(params: dict[str, Any]) -> MomentumV1:
    return MomentumV1(params)


HTF_MOMENTUM_V1_PARAMS = ParamSpace(
    Param("roc_period", "int", 5, 40, default=14),
    Param("roc_threshold", "float", 1.0, 15.0, step=1.0, default=4.0),
    Param("stop_atr_mult", "float", 1.0, 4.0, step=0.5, default=2.0),
    Param("target_atr_mult", "float", 1.0, 6.0, step=0.5, default=3.0),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
)

# coarsest-first rank for picking which available HTF snapshot to confirm on
_TF_RANK = {"5m": 0, "15m": 1, "30m": 2, "1h": 3, "4h": 4, "1d": 5, "1w": 6}


class HtfMomentumV1:
    """Higher-timeframe-confirmed momentum — the first real consumer of the
    multi-timeframe context (StrategyContext.htf, track T4). Takes the same
    ROC momentum entry as momentum_v1 but only in the direction of the HIGHER
    timeframe's trend: longs are blocked when the HTF is below its mean, shorts
    when it is above. "Trade with the bigger tide." When no HTF snapshot is
    available (engine not configured with one) it trades unconfirmed, so it
    still works on any run.

    ``htf_timeframes`` declares the coarser frames it wants; the dashboard job
    aggregates whichever are strictly coarser than the run timeframe and hands
    them back look-ahead-safe (only closed HTF bars)."""

    htf_timeframes = (Timeframe.D1, Timeframe.W1)

    def __init__(self, params: dict[str, Any]):
        self.id = "htf_momentum_v1"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        bars = ctx.snapshot.bars
        p = int(self.params["roc_period"])
        if len(bars) < p + 2:
            return []
        atr = self._atr(bars, p)
        ref = bars[-(p + 1)]
        if atr <= 0 or ref.close <= 0:
            return []
        last = bars[-1]
        roc = (last.close / ref.close - 1.0) * 100.0
        thr = float(self.params["roc_threshold"])
        stop_m = float(self.params["stop_atr_mult"])
        tgt_m = float(self.params["target_atr_mult"])
        risk = float(self.params["risk_pct"])
        bias = self._htf_bias(ctx)  # "up" | "down" | None (unconfirmed)
        open_sides = {pos.side for pos in ctx.positions}

        if roc > thr and "BUY" not in open_sides and bias != "down":
            return [self._entry("BUY", last.close - stop_m * atr,
                                last.close + tgt_m * atr, risk)]
        if (self.params["allow_short"] == "yes"
                and roc < -thr and "SELL" not in open_sides and bias != "up"):
            return [self._entry("SELL", last.close + stop_m * atr,
                                last.close - tgt_m * atr, risk)]
        return []

    def on_fill(self, fill) -> None: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...

    @staticmethod
    def _htf_bias(ctx: StrategyContext) -> str | None:
        """Trend direction of the coarsest available HTF snapshot (close vs its
        own SMA), or None when no HTF context is present."""
        if not ctx.htf:
            return None
        tf = max(ctx.htf, key=lambda t: _TF_RANK.get(t.value, 0))
        hb = ctx.htf[tf].bars
        if len(hb) < 3:
            return None
        sma = sum(b.close for b in hb) / len(hb)
        return "up" if hb[-1].close > sma else "down"

    @staticmethod
    def _atr(bars, period: int) -> float:
        recent = bars[-(period + 1):]
        trs = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
               for p, c in zip(recent, recent[1:], strict=False)]
        return sum(trs) / len(trs) if trs else 0.0

    @staticmethod
    def _entry(side, stop, target, risk) -> OrderIntent:
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),)),
            tag=f"htfmom_{side.lower()}")


@register("htf_momentum_v1", HTF_MOMENTUM_V1_PARAMS,
          description="Higher-timeframe-confirmed momentum — ROC entries taken "
                      "only in the direction of the higher timeframe's trend "
                      "(long & short). The first strategy to consult the "
                      "multi-timeframe context; trades unconfirmed if no HTF is "
                      "configured. No model calls.")
def _build_htf_momentum_v1(params: dict[str, Any]) -> HtfMomentumV1:
    return HtfMomentumV1(params)


# --- regime_momentum_v1: market-health-gated momentum (best-practice #5) ------

REGIME_MOMENTUM_V1_PARAMS = ParamSpace(
    Param("roc_period", "int", 5, 40, default=14),
    Param("roc_threshold", "float", 1.0, 15.0, step=0.5, default=5.0),
    Param("stop_atr_mult", "float", 1.5, 4.0, step=0.5, default=2.0),
    Param("target_atr_mult", "float", 1.0, 6.0, step=0.5, default=3.0),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
    Param("regime_gate", "categorical", choices=("on", "off"), default="on"),
)


class RegimeMomentumV1:
    """Rate-of-change momentum GATED by a market-health/regime read (research
    best-practice #5 + package #2: entries gated by a regime read). Entries are
    suppressed in risk-off regimes (crisis / high-volatility) and the direction
    may not oppose a trending regime — using ``analytics.classify_regime`` on
    the look-ahead-safe snapshot window (bars ≤ now). ``regime_gate=off``
    reduces to plain ROC momentum. Fixed-R ATR stop/target; long & short."""

    def __init__(self, params: dict[str, Any]):
        self.id = "regime_momentum_v1"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        from tradingagents.pro.analytics.features import classify_regime

        bars = ctx.snapshot.bars
        p = int(self.params["roc_period"])
        if len(bars) < p + 2:
            return []
        atr = MomentumV1._atr(bars, p)
        ref = bars[-(p + 1)]
        if atr <= 0 or ref.close <= 0:
            return []
        last = bars[-1]
        roc = (last.close / ref.close - 1.0) * 100.0
        thr = float(self.params["roc_threshold"])
        stop_m = float(self.params["stop_atr_mult"])
        tgt_m = float(self.params["target_atr_mult"])
        risk = float(self.params["risk_pct"])
        open_sides = {pos.side for pos in ctx.positions}

        gate_on = self.params.get("regime_gate", "on") == "on"
        regime = classify_regime(bars) if gate_on else None
        if gate_on and regime in (MarketRegime.CRISIS, MarketRegime.HIGH_VOLATILITY):
            return []  # risk-off market health → stand aside
        long_ok = not (gate_on and regime is MarketRegime.TRENDING_DOWN)
        short_ok = not (gate_on and regime is MarketRegime.TRENDING_UP)

        if roc > thr and long_ok and "BUY" not in open_sides:
            return [self._entry("BUY", last.close - stop_m * atr,
                                last.close + tgt_m * atr, risk)]
        if (self.params["allow_short"] == "yes" and short_ok
                and roc < -thr and "SELL" not in open_sides):
            return [self._entry("SELL", last.close + stop_m * atr,
                                last.close - tgt_m * atr, risk)]
        return []

    def on_fill(self, fill) -> None: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...

    @staticmethod
    def _entry(side, stop, target, risk) -> OrderIntent:
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),)),
            tag=f"regmom_{side.lower()}")


@register("regime_momentum_v1", REGIME_MOMENTUM_V1_PARAMS,
          description="Rate-of-change momentum gated by a market-health/regime "
                      "read (classify_regime): stands aside in crisis / "
                      "high-volatility regimes and won't fight a trending "
                      "regime. Native, long & short. No model calls.")
def _build_regime_momentum_v1(params: dict[str, Any]) -> RegimeMomentumV1:
    return RegimeMomentumV1(params)


__all__ = [
    "HTF_MOMENTUM_V1_PARAMS",
    "MOMENTUM_V1_PARAMS",
    "REGIME_MOMENTUM_V1_PARAMS",
    "HtfMomentumV1",
    "MomentumV1",
    "RegimeMomentumV1",
]
