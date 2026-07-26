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
    "momentum_v2": {
        # vol-relative momentum fixed the "no trades on fast bars" gap (now
        # active on every timeframe) but shows only a marginal, provisional edge
        # — one guard-passing cell, near-flat OOS Sharpe, share 0.5.
        ("BTC-USD", "4h"): Preset(params={"entry_sigma": 2.0, "roc_period": 14}, oos_sharpe=0.0044, deflated_sharpe=0.8114, pbo=0.3135, n_trials=9, evidence={"most_common_share": 0.50, "windows": 4, "note": "provisional: near-flat OOS edge"}),
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
        # ETH/SOL 1d upgraded by the finer genetic walk-forward (03_finer_search.md):
        # ~1.5-4x higher OOS Sharpe, still share 1.0 (stable across windows) and
        # guard-passing — the only finer results that met the grid presets' own
        # stability bar. Genetic-found continuous params (kept exact; verified).
        ("ETH-USD", "1d"): Preset(params={"allow_short": "yes", "lookback": 34, "risk_pct": 1.3546502028310872, "squeeze_pct": 0.14233789737754912, "stop_atr_mult": 3.548535276531993, "trail_pct": 0.012753098473019819}, oos_sharpe=0.1213, deflated_sharpe=0.8494, pbo=0.0, n_trials=60, evidence={"most_common_share": 1.00, "windows": 2, "search": "genetic-finer", "prior_grid_oos": 0.0828}),
        ("ETH-USD", "1h"): Preset(params={"lookback": 20, "squeeze_pct": 0.03}, oos_sharpe=0.0073, deflated_sharpe=0.6576, pbo=0.4167, n_trials=6, evidence={"most_common_share": 0.50, "windows": 4}),
        # C1 upgrade: a Chandelier trailing exit (Le Beau) beat the % trail here
        # (OOS 0.0535 vs 0.0338, DSR 0.993) on the enhancement re-run.
        ("ETH-USD", "4h"): Preset(params={"lookback": 20, "squeeze_pct": 0.03, "trail_mode": "chandelier"}, oos_sharpe=0.0535, deflated_sharpe=0.9934, pbo=0.0079, n_trials=12, source="docs/backtests/strategy_lab/enh/results.json", evidence={"most_common_share": 0.75, "windows": 4, "enhancement": "C1-chandelier", "prior_pct_oos": 0.0338}),
        ("SOL-USD", "1d"): Preset(params={"allow_short": "yes", "lookback": 10, "risk_pct": 0.4419642406460771, "squeeze_pct": 0.10788730469565734, "stop_atr_mult": 3.548535276531993, "trail_pct": 0.01567961396989384}, oos_sharpe=0.1232, deflated_sharpe=0.9195, pbo=0.0, n_trials=60, evidence={"most_common_share": 1.00, "windows": 2, "search": "genetic-finer", "prior_grid_oos": 0.0302}),
        # C1 upgrade: Chandelier trailing exit lifted OOS 0.0157 → 0.0705 (DSR
        # 0.990) and stability 0.50 → 0.75 versus the % trail on this cell.
        ("SOL-USD", "4h"): Preset(params={"lookback": 30, "squeeze_pct": 0.05, "trail_mode": "chandelier"}, oos_sharpe=0.0705, deflated_sharpe=0.9904, pbo=0.1865, n_trials=12, source="docs/backtests/strategy_lab/enh/results.json", evidence={"most_common_share": 0.75, "windows": 4, "enhancement": "C1-chandelier", "prior_pct_oos": 0.0157}),
    },
    "ma_crossover_v1": {
        # First guard-passing cell this strategy has earned (whipsaw kept it out
        # of the original run). NOTE: the ADX chop filter (C2) did NOT help —
        # the winner runs with adx_filter="off"; C2 is a documented negative.
        ("ETH-USD", "1d"): Preset(params={"adx_filter": "off", "fast_period": 8, "slow_period": 30}, oos_sharpe=0.0642, deflated_sharpe=0.7368, pbo=0.4127, n_trials=12, source="docs/backtests/strategy_lab/enh/results.json", evidence={"most_common_share": 0.50, "windows": 2, "note": "provisional; ADX filter off"}),
    },
    "htf_momentum_v2": {
        # C3 win: the HTF size-scaler variant earned a preset where the binary-
        # veto htf_momentum_v1 earned none. Evaluated with the scaler ACTIVE
        # (HTF = 1d/1w, as the strategy's htf_timeframes declares and the
        # dashboard job wires) — OOS 0.0311, DSR 0.847. Provisional (share 0.50).
        ("ETH-USD", "4h"): Preset(params={"roc_period": 10, "roc_threshold": 4.0}, oos_sharpe=0.0311, deflated_sharpe=0.8475, pbo=0.2302, n_trials=9, source="docs/backtests/strategy_lab/enh_htf/results.json", evidence={"most_common_share": 0.50, "windows": 4, "enhancement": "C3-htf-scaler", "htf": "1d/1w active"}),
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


def best_preset_for(strategy_id: str) -> tuple[str, str, Preset] | None:
    """The strategy's single strongest guard-passing cell — its "recommended
    best refined preset". Ranked by out-of-sample Sharpe, tie-broken by the
    deflated Sharpe (the overfitting-adjusted metric). ``None`` when the
    strategy has no preset (caller falls back to a-priori defaults). Every cell
    in CATALOG already cleared the guard bar, so the max is always shippable."""
    cells = CATALOG.get(strategy_id)
    if not cells:
        return None
    (symbol, tf), preset = max(
        cells.items(),
        key=lambda kv: (kv[1].oos_sharpe or 0.0, kv[1].deflated_sharpe or 0.0))
    return symbol, tf, preset


def recommended_presets() -> list[dict[str, Any]]:
    """One row per strategy that has a preset — its recommended best cell, for
    the UI's one-click "load best" affordance. Strategies with no preset are
    intentionally absent (the UI shows them as defaults-only)."""
    out: list[dict[str, Any]] = []
    for sid in sorted(CATALOG):
        best = best_preset_for(sid)
        if best is None:
            continue
        symbol, tf, p = best
        out.append({
            "strategy_id": sid, "symbol": symbol, "timeframe": tf,
            "params": p.params, "oos_sharpe": p.oos_sharpe,
            "deflated_sharpe": p.deflated_sharpe, "pbo": p.pbo,
        })
    return out


def list_presets() -> list[dict[str, Any]]:
    """Flat, serializable view of the catalog (for an API/UI or a report). Each
    row carries ``recommended``: true on the strategy's single best cell (see
    ``best_preset_for``), so the UI can flag/steer to the strongest option."""
    out: list[dict[str, Any]] = []
    best_cell = {
        sid: (b[0], b[1])
        for sid in CATALOG
        if (b := best_preset_for(sid)) is not None
    }
    for sid, cells in sorted(CATALOG.items()):
        for (symbol, tf), p in sorted(cells.items()):
            out.append({
                "strategy_id": sid, "symbol": symbol, "timeframe": tf,
                "params": p.params, "oos_sharpe": p.oos_sharpe,
                "deflated_sharpe": p.deflated_sharpe, "pbo": p.pbo,
                "n_trials": p.n_trials, "source": p.source,
                "recommended": best_cell.get(sid) == (symbol, tf),
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
    "best_preset_for",
    "build_preset_strategy",
    "get_preset",
    "list_presets",
    "preset_params",
    "recommended_presets",
]
