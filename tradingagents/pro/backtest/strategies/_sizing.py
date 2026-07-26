"""EXPERIMENTAL adaptive-sizing / equity-filter helpers (Strategy Optimization C4).

Two opt-in, off-by-default risk overlays for the trend strategies:

- **Kelly-capped sizing** (`kelly_risk`): scale risk-per-trade by a *fractional*
  Kelly read of the strategy's own realized round-trip outcomes, never above the
  base risk and never above the house cap. Kelly (1956) / Thorp; fractional
  Kelly is used precisely because full Kelly badly over-bets on estimation error
  — and on the small trade samples these backtests produce it is fragile, hence
  EXPERIMENTAL and floor/cap-bounded.
- **Equity-curve filter** (`equity_below_ma`): stand aside when the strategy's
  own equity is below its N-period moving average — a simple "trade the strategy
  only when its equity curve is trending up" filter (Tharp). Also EXPERIMENTAL.

Both are gated behind params that default OFF, so a strategy's shipped behaviour
is unchanged unless explicitly enabled (and only shipped as a preset if it beats
the walk-forward + DSR/PBO guard bar).
"""

from __future__ import annotations

from statistics import mean

from tradingagents.pro.analytics.risk import kelly_fraction

KELLY_CAP = 0.25          # fractional-Kelly house cap
KELLY_MIN_TRADES = 15     # need a minimum sample before trusting the estimate
KELLY_FLOOR = 0.25        # never size below floor × base risk


def kelly_risk(realized_pnls: list[float], base_risk_pct: float) -> float:
    """Fractional-Kelly-scaled risk-per-trade from realized round-trip P&Ls.
    Returns ``base_risk_pct`` unchanged until ``KELLY_MIN_TRADES`` outcomes
    exist; then scales DOWN (never up) toward the base by the capped Kelly
    fraction, flooring at ``KELLY_FLOOR × base`` so a weak edge trades small,
    not zero."""
    if len(realized_pnls) < KELLY_MIN_TRADES:
        return base_risk_pct
    wins = [p for p in realized_pnls if p > 0]
    losses = [-p for p in realized_pnls if p < 0]
    if not wins or not losses:
        return base_risk_pct
    k = kelly_fraction(len(wins) / len(realized_pnls), mean(wins), mean(losses),
                       cap=KELLY_CAP)
    if k <= 0:
        return base_risk_pct * KELLY_FLOOR
    return base_risk_pct * max(KELLY_FLOOR, min(1.0, k / KELLY_CAP))


def equity_below_ma(equity_hist: list[float], period: int) -> bool:
    """True when the latest equity is below the moving average of the prior
    ``period`` observations (so entries are suppressed while the strategy's own
    equity curve is declining). False (allow) until enough history exists."""
    if len(equity_hist) <= period:
        return False
    return equity_hist[-1] < mean(equity_hist[-period - 1:-1])
