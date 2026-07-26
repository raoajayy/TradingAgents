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


# --- momentum_v2: volatility-relative momentum (Strategy Lab gap fix) ---------

MOMENTUM_V2_PARAMS = ParamSpace(
    Param("roc_period", "int", 5, 40, default=14),
    # entry threshold in SIGMA units (vol-relative), not absolute percent — so
    # the trigger self-scales across timeframes instead of going inert on fast
    # bars the way momentum_v1's absolute roc_threshold does.
    Param("entry_sigma", "float", 1.0, 4.0, step=0.5, default=2.0),
    Param("stop_atr_mult", "float", 1.0, 4.0, step=0.5, default=2.0),
    Param("target_atr_mult", "float", 1.0, 6.0, step=0.5, default=3.0),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
)


class MomentumV2:
    """Volatility-relative momentum — the Strategy Lab found momentum_v1's
    absolute ``roc_threshold`` (a fixed percent move) makes it place NO trades on
    fast timeframes, where moves that large are vanishingly rare
    (docs/backtests/strategy_lab/01_gap_analysis.md). momentum_v2 instead
    measures the cumulative move over ``roc_period`` in units of that window's
    own return volatility (a z-score, random-walk-scaled by √period) and enters
    when |z| exceeds ``entry_sigma``. Same fixed-R ATR stop/target as v1. Because
    the trigger is relative to each timeframe's realized vol, it self-scales and
    stays active intraday. Long & short, look-ahead-safe."""

    def __init__(self, params: dict[str, Any]):
        self.id = "momentum_v2"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
        bars = ctx.snapshot.bars
        p = int(self.params["roc_period"])
        if len(bars) < p + 2:
            return []
        atr = MomentumV1._atr(bars, p)
        ref = bars[-(p + 1)]
        sigma = self._ret_sigma(bars, p)
        if atr <= 0 or ref.close <= 0 or sigma <= 0:
            return []
        last = bars[-1]
        # cumulative move over the window, normalized by expected vol (per-bar
        # sigma scaled by √period under a random-walk assumption) → a z-score
        z = (last.close / ref.close - 1.0) / (sigma * (p ** 0.5))
        thr = float(self.params["entry_sigma"])
        stop_m = float(self.params["stop_atr_mult"])
        tgt_m = float(self.params["target_atr_mult"])
        risk = float(self.params["risk_pct"])
        open_sides = {pos.side for pos in ctx.positions}

        if z > thr and "BUY" not in open_sides:
            return [self._entry("BUY", last.close - stop_m * atr,
                                last.close + tgt_m * atr, risk)]
        if (self.params["allow_short"] == "yes"
                and z < -thr and "SELL" not in open_sides):
            return [self._entry("SELL", last.close + stop_m * atr,
                                last.close - tgt_m * atr, risk)]
        return []

    def on_fill(self, fill) -> None: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...

    @staticmethod
    def _ret_sigma(bars, period: int) -> float:
        """Std-dev of per-bar simple returns over the last ``period`` bars."""
        recent = bars[-(period + 1):]
        rets = [(c.close / p.close - 1.0)
                for p, c in zip(recent, recent[1:], strict=False)
                if p.close > 0]
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        return (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5

    @staticmethod
    def _entry(side, stop, target, risk) -> OrderIntent:
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),)),
            tag=f"mom2_{side.lower()}")


@register("momentum_v2", MOMENTUM_V2_PARAMS,
          description="Volatility-relative momentum — enters when the move over "
                      "roc_period exceeds entry_sigma standard deviations of the "
                      "window's own returns (a z-score), so the trigger "
                      "self-scales across timeframes (unlike momentum_v1's "
                      "absolute threshold). Fixed-R ATR stop/target, long & "
                      "short. No model calls.")
def _build_momentum_v2(params: dict[str, Any]) -> MomentumV2:
    return MomentumV2(params)


# --- htf_momentum_v2: HTF alignment as a size SCALER, not a veto (SO-C3) ------

HTF_MOMENTUM_V2_PARAMS = ParamSpace(
    Param("roc_period", "int", 5, 40, default=14),
    Param("roc_threshold", "float", 1.0, 15.0, step=1.0, default=4.0),
    Param("stop_atr_mult", "float", 1.0, 4.0, step=0.5, default=2.0),
    Param("target_atr_mult", "float", 1.0, 6.0, step=0.5, default=3.0),
    Param("risk_pct", "float", 0.1, 3.0, step=0.1, default=1.0),
    Param("allow_short", "categorical", choices=("yes", "no"), default="yes"),
    # size multiplier when the higher timeframe strongly OPPOSES / ALIGNS with
    # the entry; the trade is scaled continuously between the two by HTF trend
    # strength (close-vs-mean), rather than hard-vetoed as in htf_momentum_v1.
    Param("htf_scale_min", "float", 0.0, 1.0, step=0.25, default=0.25),
    Param("htf_scale_max", "float", 1.0, 3.0, step=0.5, default=1.5),
    Param("htf_ref_dev", "float", 0.02, 0.20, step=0.02, default=0.05),
)


class HtfMomentumV2:
    """Higher-timeframe momentum where HTF alignment SCALES position size
    instead of vetoing the trade (SO-C3). ``htf_momentum_v1`` blocks longs when
    the HTF is below its mean and shorts when above — a binary veto that earned
    zero tuned presets because it mostly just removed trades. v2 keeps every ROC
    entry but sizes it by HTF conviction: ``risk_pct`` scales continuously from
    ``htf_scale_min`` (HTF strongly opposes) through 1.0 (neutral / no HTF) up to
    ``htf_scale_max`` (HTF strongly aligns), where "strength" is the HTF close's
    signed distance from its own mean normalized by ``htf_ref_dev``. Fixed-R ATR
    stop/target; long & short; look-ahead-safe. Evidence: multi-timeframe
    confirmation + trend-strength position sizing (Kaufman)."""

    htf_timeframes = (Timeframe.D1, Timeframe.W1)

    def __init__(self, params: dict[str, Any]):
        self.id = "htf_momentum_v2"
        self.params = params

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> list[OrderIntent]:
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
        base = float(self.params["risk_pct"])
        open_sides = {pos.side for pos in ctx.positions}

        if roc > thr and "BUY" not in open_sides:
            return [self._entry("BUY", last.close - stop_m * atr,
                                last.close + tgt_m * atr,
                                base * self._htf_scale(ctx, "BUY"))]
        if (self.params["allow_short"] == "yes"
                and roc < -thr and "SELL" not in open_sides):
            return [self._entry("SELL", last.close + stop_m * atr,
                                last.close - tgt_m * atr,
                                base * self._htf_scale(ctx, "SELL"))]
        return []

    def on_fill(self, fill) -> None: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...

    def _htf_scale(self, ctx: StrategyContext, side: str) -> float:
        """Continuous size multiplier from HTF alignment. 1.0 when no HTF
        context; else scales min→max by the signed HTF close-vs-mean deviation
        (in the entry's direction) normalized by htf_ref_dev, clipped."""
        smin = float(self.params["htf_scale_min"])
        smax = float(self.params["htf_scale_max"])
        if not ctx.htf:
            return 1.0
        tf = max(ctx.htf, key=lambda t: _TF_RANK.get(t.value, 0))
        hb = ctx.htf[tf].bars
        if len(hb) < 3:
            return 1.0
        sma = sum(b.close for b in hb) / len(hb)
        if sma <= 0:
            return 1.0
        dev = hb[-1].close / sma - 1.0                 # signed HTF trend strength
        aligned = dev if side == "BUY" else -dev       # >0 aligns with the entry
        ref = float(self.params["htf_ref_dev"])
        score = 0.5 + aligned / (2.0 * ref)            # 0 (opposed) .. 1 (aligned)
        score = max(0.0, min(1.0, score))
        return smin + (smax - smin) * score

    @staticmethod
    def _entry(side, stop, target, risk) -> OrderIntent:
        return OrderIntent(
            kind="market", side=side, risk_pct=risk,
            bracket=BracketIntent(stop_loss=stop, take_profits=((target, 1.0),)),
            tag=f"htfmom2_{side.lower()}")


@register("htf_momentum_v2", HTF_MOMENTUM_V2_PARAMS,
          description="Higher-timeframe momentum where HTF alignment SCALES "
                      "position size (min→max by HTF trend strength) instead of "
                      "vetoing the trade as htf_momentum_v1 does. Fixed-R ATR "
                      "stop/target, long & short. No model calls.")
def _build_htf_momentum_v2(params: dict[str, Any]) -> HtfMomentumV2:
    return HtfMomentumV2(params)


__all__ = [
    "HTF_MOMENTUM_V1_PARAMS",
    "HTF_MOMENTUM_V2_PARAMS",
    "MOMENTUM_V1_PARAMS",
    "MOMENTUM_V2_PARAMS",
    "REGIME_MOMENTUM_V1_PARAMS",
    "HtfMomentumV1",
    "HtfMomentumV2",
    "MomentumV1",
    "MomentumV2",
    "RegimeMomentumV1",
]
