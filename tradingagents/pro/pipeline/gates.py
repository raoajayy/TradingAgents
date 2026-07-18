"""Deterministic gates: code decides pass/fail, LLMs never override them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from tradingagents.contracts import MetricReading, ProConfig, TradeAction


@dataclass(frozen=True)
class GateResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


def risk_gate(
    risk_metrics: dict[str, MetricReading],
    config: ProConfig,
    proposed_action: TradeAction | None = None,
) -> GateResult:
    """Hard risk checks before any qualitative judgment happens.

    - VaR(95) per bar must not exceed the configured max daily loss.
    - CVaR must not exceed twice the daily-loss limit (tail sanity).
    - A directional trade needs engine levels (entry/stop) to exist —
      without them Constraint 2 leaves nothing valid to execute against.
    """
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    var = risk_metrics.get("VAR_95")
    daily_limit = config.risk.max_daily_loss_pct / 100.0
    if var is None:
        checks["var_available"] = False
        reasons.append("VAR_95 unavailable (insufficient return history)")
    else:
        checks["var_available"] = True
        checks["var_within_limit"] = var.value <= daily_limit
        if not checks["var_within_limit"]:
            reasons.append(
                f"VaR95 {var.value:.4f} exceeds max daily loss {daily_limit:.4f}"
            )

    cvar = risk_metrics.get("CVAR_95")
    if cvar is not None:
        checks["cvar_within_limit"] = cvar.value <= 2 * daily_limit
        if not checks["cvar_within_limit"]:
            reasons.append(
                f"CVaR95 {cvar.value:.4f} exceeds 2x max daily loss {2 * daily_limit:.4f}"
            )

    if proposed_action in (TradeAction.BUY, TradeAction.SELL):
        has_levels = "ATR_STOP" in risk_metrics and "ENTRY_REF_PRICE" in risk_metrics
        checks["levels_available"] = has_levels
        if not has_levels:
            reasons.append("no engine-computed entry/stop levels (ATR missing)")

    passed = all(checks.values()) if checks else False
    if not checks:
        reasons.append("risk gate had nothing to check; failing closed")
    return GateResult(passed=passed, checks=checks, reasons=tuple(reasons))


def event_gate(
    next_major: dict | None,
    now: datetime,
    block_hours: float,
    calendar_available: bool = True,
) -> GateResult:
    """No NEW entries inside the pre-event window of a major release.

    The trader review's deal-breaker #2: the pipeline shorted gold on FOMC
    day and none of its 41 evidence items mentioned the Fed. This gate is
    the structural fix — within ``block_hours`` of a scheduled major event
    (calendar's ``next_major``: FOMC/CPI/NFP/...), the run declines to open
    anything new. Exits are never blocked (this runs pre-entry only).

    CI-4 fail-closed: ``calendar_available=False`` means the operator wired a
    calendar but the feed is DOWN this run. We cannot confirm the window is
    clear, so we refuse new entries rather than silently pass open — the exact
    failure (a broken calendar re-enabling FOMC-day trading) this gate exists
    to prevent. A genuinely empty calendar (``next_major`` is None with the
    feed healthy) still passes: no event means no window to block.
    """
    if block_hours <= 0:
        return GateResult(passed=True, checks={"event_window_clear": True})
    if not calendar_available:
        return GateResult(
            passed=False,
            checks={"calendar_available": False},
            reasons=(
                "economic calendar unavailable — cannot confirm the pre-event "
                "window is clear; refusing new entries until the feed recovers "
                "(exits are unaffected)",
            ),
        )
    if not next_major:
        return GateResult(passed=True, checks={"event_window_clear": True})
    at_raw = next_major.get("at") or next_major.get("ts_utc")
    if not at_raw:
        # date-only event: blocking whole days would be worse than the
        # disease; the debate prompt still sees the calendar context
        return GateResult(passed=True, checks={"event_window_clear": True})
    try:
        instant = datetime.fromisoformat(str(at_raw))
        seconds = (instant - now).total_seconds()
    except (TypeError, ValueError):
        return GateResult(passed=True, checks={"event_window_clear": True})
    if 0 <= seconds <= block_hours * 3600:
        release = next_major.get("release", "major scheduled event")
        return GateResult(
            passed=False,
            checks={"event_window_clear": False},
            reasons=(
                f"{release} in {seconds / 3600:.1f}h — new entries are "
                f"blocked within {block_hours:g}h of major scheduled events",
            ),
        )
    return GateResult(passed=True, checks={"event_window_clear": True})
