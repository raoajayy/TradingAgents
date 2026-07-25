"""Tuned parameter presets (Strategy Lab) — additive and non-destructive.

Each preset is a per-(strategy, symbol, timeframe) parameter set that beat its
strategy's a-priori defaults on **walk-forward out-of-sample** data AND survived
the overfitting guard bar (deflated Sharpe ≥ 0.6, PBO ≤ 0.5, OOS Sharpe > 0).
Presets are chosen by ``scripts/pro_strategy_lab.py`` from real vendor history;
each entry cites its evidence (OOS Sharpe / DSR / PBO / n_trials / source run).

This module NEVER changes a strategy's shipped ``Param`` defaults — those stay
the a-priori constants (12_validation_methodology.md Part 3), so the equivalence
golden and every default-assertion test are untouched. A preset is an *opt-in*
overlay: callers ask for one explicitly via ``preset_params`` /
``build_preset_strategy``. A missing (strategy, symbol, timeframe) returns
``None`` — the caller then just uses defaults.

Only cells that earned a preset appear here; strategies/markets where nothing
cleared the guard bar are intentionally absent (documented in
docs/research/17_strategy_lab.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tradingagents.pro.backtest.registry import build_strategy, strategy_param_space


@dataclass(frozen=True)
class Preset:
    """A guard-passing tuned parameter set + the OOS evidence that earned it."""

    params: dict[str, Any]
    oos_sharpe: float
    deflated_sharpe: float
    pbo: float
    n_trials: int
    source: str = "docs/backtests/strategy_lab/results.json"
    evidence: dict[str, Any] = field(default_factory=dict)


# catalog[strategy_id][(symbol, timeframe)] -> Preset
# Populated from the Strategy Lab run (docs/backtests/strategy_lab/results.json,
# 2026-07-25): only cells that cleared the guard bar (DSR≥0.6, PBO≤0.5,
# OOS-Sharpe>0). All winners are 4h/1d crypto (ETH/SOL/BTC); no fast-timeframe
# or gold cell earned a preset. See docs/research/17_strategy_lab.md.
CATALOG: dict[str, dict[tuple[str, str], Preset]] = {
    "mean_reversion_v1": {
        ("BTC-USD", "4h"): Preset(params={"entry_std": 2.5, "lookback": 30, "stop_atr_mult": 2.0}, oos_sharpe=0.0018, deflated_sharpe=0.7305, pbo=0.3810, n_trials=8, evidence={"most_common_share": 0.50, "windows": 4}),
        ("SOL-USD", "1d"): Preset(params={"entry_std": 2.5, "lookback": 30, "stop_atr_mult": 2.0}, oos_sharpe=0.0192, deflated_sharpe=0.6809, pbo=0.2579, n_trials=8, evidence={"most_common_share": 0.50, "windows": 2}),
    },
    "momentum_v1": {
        ("ETH-USD", "4h"): Preset(params={"roc_period": 14, "roc_threshold": 3.0}, oos_sharpe=0.0250, deflated_sharpe=0.8543, pbo=0.1190, n_trials=9, evidence={"most_common_share": 0.50, "windows": 4}),
    },
    "regime_momentum_v1": {
        ("ETH-USD", "1d"): Preset(params={"regime_gate": "off", "roc_period": 20, "roc_threshold": 5.0}, oos_sharpe=0.0669, deflated_sharpe=0.6678, pbo=0.3095, n_trials=8, evidence={"most_common_share": 1.00, "windows": 2}),
        ("ETH-USD", "4h"): Preset(params={"regime_gate": "on", "roc_period": 10, "roc_threshold": 3.0}, oos_sharpe=0.0403, deflated_sharpe=0.9175, pbo=0.0397, n_trials=8, evidence={"most_common_share": 0.75, "windows": 4}),
    },
    "trend_following_v1": {
        ("ETH-USD", "1d"): Preset(params={"donchian_period": 20, "stop_atr_mult": 2.0, "trail_pct": 0.05}, oos_sharpe=0.0749, deflated_sharpe=0.9798, pbo=0.2063, n_trials=8, evidence={"most_common_share": 1.00, "windows": 2}),
        ("SOL-USD", "4h"): Preset(params={"donchian_period": 50, "stop_atr_mult": 2.0, "trail_pct": 0.08}, oos_sharpe=0.0243, deflated_sharpe=0.7712, pbo=0.0833, n_trials=8, evidence={"most_common_share": 0.50, "windows": 4}),
    },
    "trend_following_v2": {
        ("ETH-USD", "1d"): Preset(params={"add_atr_mult": 1.0, "donchian_period": 20, "max_adds": 2}, oos_sharpe=0.0970, deflated_sharpe=0.9984, pbo=0.1508, n_trials=8, evidence={"most_common_share": 1.00, "windows": 2}),
        ("ETH-USD", "4h"): Preset(params={"add_atr_mult": 2.0, "donchian_period": 40, "max_adds": 2}, oos_sharpe=0.0367, deflated_sharpe=0.9039, pbo=0.0714, n_trials=8, evidence={"most_common_share": 0.75, "windows": 4}),
        ("SOL-USD", "1d"): Preset(params={"add_atr_mult": 2.0, "donchian_period": 40, "max_adds": 2}, oos_sharpe=0.0575, deflated_sharpe=0.7648, pbo=0.1429, n_trials=8, evidence={"most_common_share": 0.50, "windows": 2}),
        ("SOL-USD", "4h"): Preset(params={"add_atr_mult": 1.0, "donchian_period": 40, "max_adds": 0}, oos_sharpe=0.0176, deflated_sharpe=0.6682, pbo=0.1349, n_trials=8, evidence={"most_common_share": 1.00, "windows": 4}),
    },
    "volatility_breakout_v1": {
        ("ETH-USD", "1d"): Preset(params={"lookback": 20, "squeeze_pct": 0.08}, oos_sharpe=0.0828, deflated_sharpe=0.9373, pbo=0.0119, n_trials=6, evidence={"most_common_share": 1.00, "windows": 2}),
        ("ETH-USD", "1h"): Preset(params={"lookback": 20, "squeeze_pct": 0.03}, oos_sharpe=0.0073, deflated_sharpe=0.6576, pbo=0.4167, n_trials=6, evidence={"most_common_share": 0.50, "windows": 4}),
        ("ETH-USD", "4h"): Preset(params={"lookback": 30, "squeeze_pct": 0.08}, oos_sharpe=0.0338, deflated_sharpe=0.9191, pbo=0.1786, n_trials=6, evidence={"most_common_share": 0.75, "windows": 4}),
        ("SOL-USD", "1d"): Preset(params={"lookback": 20, "squeeze_pct": 0.08}, oos_sharpe=0.0302, deflated_sharpe=0.8104, pbo=0.2540, n_trials=6, evidence={"most_common_share": 1.00, "windows": 2}),
        ("SOL-USD", "4h"): Preset(params={"lookback": 30, "squeeze_pct": 0.05}, oos_sharpe=0.0157, deflated_sharpe=0.9008, pbo=0.3810, n_trials=6, evidence={"most_common_share": 0.50, "windows": 4}),
    },
}


def preset_params(strategy_id: str, symbol: str, timeframe: str) -> dict[str, Any] | None:
    """The tuned params for this (strategy, symbol, timeframe), or None if no
    guard-passing preset exists (caller should fall back to defaults)."""
    entry = CATALOG.get(strategy_id, {}).get((symbol, timeframe))
    return dict(entry.params) if entry is not None else None


def get_preset(strategy_id: str, symbol: str, timeframe: str) -> Preset | None:
    """The full Preset (params + evidence), or None."""
    return CATALOG.get(strategy_id, {}).get((symbol, timeframe))


def list_presets() -> list[dict[str, Any]]:
    """Flat, serializable view of the catalog (for an API/UI or a report)."""
    out: list[dict[str, Any]] = []
    for sid, cells in sorted(CATALOG.items()):
        for (symbol, tf), p in sorted(cells.items()):
            out.append({
                "strategy_id": sid, "symbol": symbol, "timeframe": tf,
                "params": p.params, "oos_sharpe": p.oos_sharpe,
                "deflated_sharpe": p.deflated_sharpe, "pbo": p.pbo,
                "n_trials": p.n_trials, "source": p.source,
            })
    return out


def build_preset_strategy(strategy_id: str, symbol: str, timeframe: str,
                          extra_overrides: dict[str, Any] | None = None):
    """Build a strategy instance using its tuned preset (if one exists) layered
    over the a-priori defaults; ``extra_overrides`` win over the preset. Falls
    back to pure defaults when no preset is registered for the cell."""
    overrides: dict[str, Any] = preset_params(strategy_id, symbol, timeframe) or {}
    if extra_overrides:
        overrides.update(extra_overrides)
    return build_strategy(strategy_id, overrides or None)


def _validate_catalog() -> None:
    """Every preset value must sit inside its strategy's DECLARED param domain
    (a preset can only pick values the strategy already exposes). Not run at
    import (this module loads before the built-in strategies register, per
    backtest/__init__.py); ``tests/test_pro_presets.py`` calls it as the gate."""
    for sid, cells in CATALOG.items():
        space = strategy_param_space(sid)  # raises if strategy_id unknown
        for (symbol, tf), preset in cells.items():
            for name, value in preset.params.items():
                if name not in space:
                    raise ValueError(
                        f"preset {sid}@{symbol}/{tf}: unknown param {name!r}")
                if not space._by_name[name].contains(value):
                    raise ValueError(
                        f"preset {sid}@{symbol}/{tf}: {name}={value!r} "
                        "outside declared domain")


__all__ = [
    "CATALOG",
    "Preset",
    "build_preset_strategy",
    "get_preset",
    "list_presets",
    "preset_params",
]
