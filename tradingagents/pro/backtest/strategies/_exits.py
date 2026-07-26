"""Shared trailing-exit helpers (Strategy Optimization C1).

The broker already implements three trailing modes (`SimBroker._update_trailing`:
``pct`` / ``atr`` / ``chandelier``) but no strategy used anything but ``pct``.
These helpers let the trailing strategies opt into an ATR-distance trailing stop
or a Chandelier exit (rolling-high/low − mult×ATR) via a ``trail_mode`` param,
**without changing their default behaviour** — the default is still ``pct`` with
``trailing_mult = trail_pct``, so equivalence and shipped defaults are untouched.

Evidence: the Chandelier Exit (Charles Le Beau) and ATR-distance trailing stops
are standard trend-following exit techniques (Kaufman, *Trading Systems and
Methods*); an exit that widens with realized volatility avoids being shaken out
by noise while still ratcheting to protect open profit.
"""

from __future__ import annotations

from typing import Any

from tradingagents.pro.backtest.strategy import Param

# ATR/chandelier lookback bound so the trailing window stays within the engine
# warm-up; multiplier range brackets the classic Chandelier default (~3×ATR).
TRAIL_MODES = ("pct", "atr", "chandelier")


def exit_param_specs() -> tuple[Param, ...]:
    """The additive, off-by-default trailing params shared by trailing
    strategies. Appended to a strategy's ParamSpace; ``trail_mode='pct'`` (the
    default) reproduces the prior geometry exactly."""
    return (
        Param("trail_mode", "categorical", choices=TRAIL_MODES, default="pct"),
        Param("trail_atr_mult", "float", 1.5, 5.0, step=0.5, default=3.0),
        Param("trail_period", "int", 10, 40, default=22),
    )


def trailing_fields(params: dict[str, Any]) -> dict[str, Any]:
    """BracketIntent trailing kwargs for a strategy's params. ``pct`` →
    ``trailing_mult = trail_pct`` (unchanged); ``atr``/``chandelier`` →
    ``trail_atr_mult`` × ATR over ``trail_period`` (the broker's modes)."""
    mode = params.get("trail_mode", "pct")
    if mode == "pct":
        return {"trailing": "pct", "trailing_mult": float(params["trail_pct"])}
    return {
        "trailing": mode,
        "trailing_mult": float(params.get("trail_atr_mult", 3.0)),
        "trailing_period": int(params.get("trail_period", 22)),
    }
