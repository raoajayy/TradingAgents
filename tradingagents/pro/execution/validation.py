"""Deterministic order validation — the last code gate before an adapter.

The TradeRecommendation contract already guarantees geometry; this layer
checks execution-time concerns: freshness, size vs limits, venue support,
and that nobody is trying to execute a HOLD. P2-05 adds portfolio-level
caps (parametric VaR and correlated gross exposure) evaluated against the
book the order would create.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tradingagents.contracts import RiskLimits, TradeAction, TradeRecommendation, utc_now
from tradingagents.pro.analytics.risk import portfolio_var

MAX_AGE_MINUTES = 60
# P2-05: |return correlation| above this makes two positions one bet
CORRELATED_RHO = 0.6
PORTFOLIO_VAR_CONFIDENCE = 0.99


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioRiskContext:
    """Book-level inputs for the P2-05 caps.

    ``open_notional_by_symbol`` holds SIGNED notionals (quote currency,
    negative = short) for open positions. ``cov_symbols`` / ``covariance``
    is the daily log-return covariance from
    ``analytics.risk.returns_covariance`` (same symbol order). Missing
    covariance or symbols absent from it fail OPEN — an unmeasurable book
    is a data gap to disclose, not a veto — and both limits default to
    None, so nothing changes until an operator opts in.
    """

    open_notional_by_symbol: Mapping[str, float] = field(default_factory=dict)
    cov_symbols: tuple[str, ...] = ()
    covariance: Sequence[Sequence[float]] | None = None


def _pairwise_rho(cov, i: int, j: int) -> float | None:
    var_i, var_j = float(cov[i][i]), float(cov[j][j])
    if var_i <= 0 or var_j <= 0:
        return None
    return float(cov[i][j]) / math.sqrt(var_i * var_j)


def portfolio_risk_reasons(
    symbol: str,
    side: str,
    notional: float,
    equity: float,
    limits: RiskLimits,
    portfolio: PortfolioRiskContext | None,
) -> list[str]:
    """P2-05 pre-trade caps: refuse a NEW position that would push
    (a) parametric portfolio VaR past ``limits.max_portfolio_var_pct`` or
    (b) gross exposure of pairwise-correlated assets (|rho| > 0.6) past
    ``limits.max_correlated_gross_pct``. Empty list = no objection."""
    var_limit = getattr(limits, "max_portfolio_var_pct", None)
    corr_limit = getattr(limits, "max_correlated_gross_pct", None)
    if portfolio is None or (var_limit is None and corr_limit is None):
        return []
    if not equity or equity <= 0 or notional <= 0:
        return []
    cov = portfolio.covariance
    index = {s: i for i, s in enumerate(portfolio.cov_symbols)}
    if cov is None or symbol not in index:
        return []  # no return history for the candidate — fail open
    reasons: list[str] = []
    open_book = dict(portfolio.open_notional_by_symbol)
    signed = notional if side == "BUY" else -notional

    if var_limit is not None:
        book = dict(open_book)
        book[symbol] = book.get(symbol, 0.0) + signed
        held = [s for s in book if s in index and book[s]]
        sub = [[float(cov[index[a]][index[b]]) for b in held] for a in held]
        weights = [book[s] / equity for s in held]
        var = portfolio_var(weights, sub, confidence=PORTFOLIO_VAR_CONFIDENCE)
        if var is not None and var * 100.0 > var_limit + 1e-9:
            reasons.append(
                f"portfolio VaR would reach {var * 100.0:.2f}% of equity "
                f"(1-day, {PORTFOLIO_VAR_CONFIDENCE:.0%}), over the "
                f"{var_limit}% limit"
            )

    if corr_limit is not None:
        i = index[symbol]
        gross = abs(signed) + abs(open_book.get(symbol, 0.0))
        peers: list[str] = []
        for held_symbol, held_notional in open_book.items():
            if held_symbol == symbol or not held_notional:
                continue
            j = index.get(held_symbol)
            if j is None:
                continue  # peer without history cannot be assessed
            rho = _pairwise_rho(cov, i, j)
            if rho is not None and abs(rho) > CORRELATED_RHO:
                gross += abs(held_notional)
                peers.append(f"{held_symbol} (rho {rho:+.2f})")
        gross_pct = gross / equity * 100.0
        if peers and gross_pct > corr_limit + 1e-9:
            reasons.append(
                f"correlated gross exposure would reach {gross_pct:.1f}% of "
                f"equity with {', '.join(peers)} "
                f"(|rho| > {CORRELATED_RHO}), over the {corr_limit}% limit"
            )
    return reasons


def validate_recommendation(
    rec: TradeRecommendation | None,
    limits: RiskLimits,
    equity: float,
    supported_symbols: set[str],
    max_age_minutes: int = MAX_AGE_MINUTES,
    portfolio: PortfolioRiskContext | None = None,
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
    if rec.action in (TradeAction.BUY, TradeAction.SELL):
        reasons.extend(portfolio_risk_reasons(
            rec.symbol, rec.action.value, notional, equity, limits, portfolio,
        ))
    return ValidationResult(not reasons, tuple(reasons))
