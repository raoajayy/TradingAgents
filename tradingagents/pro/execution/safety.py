"""Kill switch and circuit breakers (Constraint 5).

Both are latching: once tripped they refuse new entries until a human (or
an explicit operator action) resets them. The kill switch is file-backed
so an operator can engage it from a shell — ``touch`` the file — without
touching the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tradingagents.contracts import RiskLimits, utc_now


class KillSwitch:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._engaged = False
        self._reason = ""

    @property
    def engaged(self) -> bool:
        if self._engaged:
            return True
        if self._path is not None and self._path.exists():
            self._engaged = True
            self._reason = self._reason or f"kill file present: {self._path}"
            return True
        return False

    @property
    def reason(self) -> str:
        return self._reason

    def engage(self, reason: str) -> None:
        self._engaged = True
        self._reason = reason
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(f"{utc_now().isoformat()} {reason}\n", encoding="utf-8")

    def reset(self, operator: str) -> None:
        """Explicit human action; there is no automatic reset."""
        if not operator:
            raise ValueError("reset requires an operator identity")
        self._engaged = False
        self._reason = ""
        if self._path is not None and self._path.exists():
            self._path.unlink()


@dataclass(frozen=True)
class BreakerState:
    tripped: bool
    reason: str = ""


class CircuitBreaker:
    """Halts *new entries* on consecutive losses or daily-loss breach.

    Trip conditions come from RiskLimits (Phase 0). Latching per day: a
    tripped breaker stays tripped until ``reset`` or the trading day rolls
    over (the daily-loss counter resets with the day; consecutive losses
    do not — losing streaks span days).
    """

    def __init__(self, limits: RiskLimits, equity_base: float):
        if equity_base <= 0:
            raise ValueError("equity_base must be positive")
        self.limits = limits
        self.equity_base = equity_base
        # live equity + high-water mark (CI-3/§8): the daily-loss dollar trip
        # and the account-drawdown trip measure against live equity, not a
        # frozen base. Both default to equity_base until update_equity runs.
        self.current_equity = equity_base
        self.high_water_mark = equity_base
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self._day: date | None = None
        self._tripped_reason = ""

    def update_equity(self, equity: float) -> None:
        """Feed live account equity: raises the high-water mark and trips the
        breaker on a max_drawdown_pct breach (latching; drawdown from the peak
        does not reset with the trading day, unlike the daily-loss counter)."""
        if equity <= 0:
            return
        self.current_equity = equity
        if equity > self.high_water_mark:
            self.high_water_mark = equity
        drawdown_pct = 100.0 * (self.high_water_mark - equity) / self.high_water_mark
        if drawdown_pct >= self.limits.max_drawdown_pct:
            self._tripped_reason = (
                f"drawdown {drawdown_pct:.2f}% from high-water mark "
                f"{self.high_water_mark:.2f} breached limit "
                f"{self.limits.max_drawdown_pct}%"
            )

    def _roll_day(self, today: date) -> None:
        if self._day != today:
            self._day = today
            self.daily_pnl = 0.0
            if self._tripped_reason.startswith("daily loss"):
                self._tripped_reason = ""

    def record_trade_result(self, pnl: float, today: date | None = None) -> None:
        self._roll_day(today or utc_now().date())
        self.daily_pnl += pnl
        self.consecutive_losses = 0 if pnl > 0 else self.consecutive_losses + 1

        if self.consecutive_losses >= self.limits.circuit_breaker_consecutive_losses:
            self._tripped_reason = (
                f"{self.consecutive_losses} consecutive losses "
                f"(limit {self.limits.circuit_breaker_consecutive_losses})"
            )
        daily_limit = self.current_equity * self.limits.max_daily_loss_pct / 100
        if -self.daily_pnl >= daily_limit:
            self._tripped_reason = (
                f"daily loss {-self.daily_pnl:.2f} breached limit {daily_limit:.2f}"
            )

    def check(self, today: date | None = None) -> BreakerState:
        self._roll_day(today or utc_now().date())
        return BreakerState(bool(self._tripped_reason), self._tripped_reason)

    def reset(self, operator: str) -> None:
        if not operator:
            raise ValueError("reset requires an operator identity")
        self._tripped_reason = ""
        self.consecutive_losses = 0
