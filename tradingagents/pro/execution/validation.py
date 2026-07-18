"""Deterministic order validation — the last code gate before an adapter.

The TradeRecommendation contract already guarantees geometry; this layer
checks execution-time concerns: freshness, size vs limits, venue support,
and that nobody is trying to execute a HOLD.
"""

from __future__ import annotations

from dataclasses import dataclass

from tradingagents.contracts import RiskLimits, TradeAction, TradeRecommendation, utc_now

MAX_AGE_MINUTES = 60


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...] = ()


def validate_recommendation(
    rec: TradeRecommendation | None,
    limits: RiskLimits,
    equity: float,
    supported_symbols: set[str],
    max_age_minutes: int = MAX_AGE_MINUTES,
    open_positions: int = 0,
) -> ValidationResult:
    reasons: list[str] = []
    if rec is None:
        return ValidationResult(False, ("no recommendation to execute",))
    if rec.action is TradeAction.HOLD:
        reasons.append("HOLD is not executable")
    if rec.symbol not in supported_symbols:
        reasons.append(f"symbol {rec.symbol} not supported by this venue")
    age_minutes = (utc_now() - rec.created_at).total_seconds() / 60
    if age_minutes > max_age_minutes:
        reasons.append(
            f"recommendation is {age_minutes:.0f} min old (max {max_age_minutes}); "
            "market state has moved on"
        )
    if rec.position_size.quantity <= 0:
        reasons.append("non-positive quantity")
    notional = rec.position_size.notional or (
        rec.position_size.quantity * (rec.entry_price or 0)
    )
    cap = equity * limits.max_position_pct_equity / 100 * limits.max_leverage
    if notional > cap * (1 + 1e-9):
        reasons.append(
            f"notional {notional:.2f} exceeds cap {cap:.2f} "
            f"({limits.max_position_pct_equity}% equity x {limits.max_leverage}x)"
        )
    # RiskLimits.max_open_positions enforced on EVERY path (gap CI-3): the
    # live gate chain enforced this for live capital only; paper orders never
    # saw it. Refuse a new entry once the book already holds the cap.
    if open_positions >= limits.max_open_positions:
        reasons.append(
            f"open positions {open_positions} at cap {limits.max_open_positions}"
        )
    # RiskLimits.max_risk_per_trade_pct enforced on EVERY path (gap CI-3): the
    # stop distance bounds the worst-case loss; a ticket risking more than the
    # per-trade budget is refused even in paper (defense in depth over the
    # engine's sizing).
    if (
        rec.entry_price is not None
        and rec.stop_loss is not None
        and equity > 0
    ):
        risk_amount = abs(rec.entry_price - rec.stop_loss) * rec.position_size.quantity
        risk_pct = 100.0 * risk_amount / equity
        if risk_pct > limits.max_risk_per_trade_pct * (1 + 1e-9):
            reasons.append(
                f"per-trade risk {risk_pct:.2f}% exceeds limit "
                f"{limits.max_risk_per_trade_pct}%"
            )
    return ValidationResult(not reasons, tuple(reasons))
