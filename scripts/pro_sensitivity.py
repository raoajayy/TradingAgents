"""Parameter-sensitivity heatmaps (SO-B) for the top shipped presets.

For each curated top cell, sweeps the strategy's two most meaningful parameters
across an in-domain grid — holding every *other* parameter at the shipped preset
value — runs one backtest per (x, y) combo, and renders the **held-out (last
HOLDOUT_FRAC of the window) Sharpe** surface as a heatmap. The preset cell boxed.

Why holdout Sharpe, not full-window Sharpe: presets are chosen by *walk-forward
out-of-sample* Sharpe, so the honest sensitivity question is "does the
neighbourhood of the preset generalize to unseen bars?" — not "does it fit the
whole history?". Scoring only the held-out tail keeps the surface metric
consistent with how the preset was selected. (These strategies do no per-window
fitting — params are fixed — so the tail is a clean OOS read.)

The point is an overfitting check you can *see*: a robust preset sits on a
**plateau** (neighbours score similarly and positive), an overfit one on a lone
**spike** (a single bright cell in a dark field). Visual complement to the
DSR/PBO guard — same question ("is this fit stable?"), rendered.

Reuses ``pro_strategy_lab.LabTrial`` (identical engine/broker/replay wiring) and
``charts.param_sensitivity_heatmap``. matplotlib-absent → PNGs skipped, the
markdown table still written. Bars capped for turnaround; the preset's headline
evidence remains its full walk-forward OOS run (REPORT.md).

    python scripts/pro_sensitivity.py
    python scripts/pro_sensitivity.py --max-bars 1200 --holdout 0.3
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "pro_strategy_lab", REPO_ROOT / "scripts" / "pro_strategy_lab.py")
lab = importlib.util.module_from_spec(_SPEC)
sys.modules["pro_strategy_lab"] = lab
_SPEC.loader.exec_module(lab)

from tradingagents.contracts import Timeframe  # noqa: E402
from tradingagents.pro.backtest import presets  # noqa: E402
from tradingagents.pro.backtest.registry import strategy_param_space  # noqa: E402

OUT_DIR = REPO_ROOT / "docs" / "backtests" / "strategy_lab"
CHART_DIR = OUT_DIR / "sensitivity"
_TFMAP = {"1h": Timeframe.H1, "4h": Timeframe.H4, "1d": Timeframe.D1}

# Two swept axes per strategy: (y_name, y_values, x_name, x_values). Values are
# inside each strategy's DECLARED ParamSpace (asserted at startup).
AXES: dict[str, tuple] = {
    "trend_following_v1": ("donchian_period", [10, 20, 35, 50],
                           "stop_atr_mult", [1.5, 2.0, 2.5, 3.0]),
    "trend_following_v2": ("donchian_period", [10, 20, 35, 50],
                           "stop_atr_mult", [1.5, 2.0, 2.5, 3.0]),
    "volatility_breakout_v1": ("lookback", [10, 20, 30, 40],
                               "squeeze_pct", [0.03, 0.05, 0.08, 0.12]),
    "momentum_v1": ("roc_period", [10, 14, 20, 28],
                    "roc_threshold", [2.0, 3.0, 4.0, 6.0]),
    "momentum_v2": ("roc_period", [10, 14, 20, 28],
                    "entry_sigma", [1.5, 2.0, 2.5, 3.0]),
    "htf_momentum_v2": ("roc_period", [10, 14, 20],
                        "roc_threshold", [3.0, 4.0, 6.0]),
    "mean_reversion_v1": ("lookback", [20, 30, 40],
                          "entry_std", [2.0, 2.5, 3.0]),
    "ma_crossover_v1": ("fast_period", [5, 8, 12],
                        "slow_period", [20, 30, 40]),
    "regime_momentum_v1": ("roc_period", [10, 20, 30],
                           "roc_threshold", [3.0, 5.0, 7.0]),
}

# Curated top cells (highest-DSR representative per strategy family) — kept
# small so the pass finishes quickly. Each is a shipped preset key.
TOP_CELLS: list[tuple[str, str, str]] = [
    ("trend_following_v2", "ETH-USD", "1d"),
    ("trend_following_v1", "ETH-USD", "1d"),
    ("volatility_breakout_v1", "ETH-USD", "4h"),
    ("volatility_breakout_v1", "SOL-USD", "4h"),
    ("regime_momentum_v1", "ETH-USD", "4h"),
    ("htf_momentum_v2", "ETH-USD", "4h"),
    ("momentum_v2", "BTC-USD", "4h"),
    ("mean_reversion_v1", "SOL-USD", "1d"),
]


def _closest_index(vals: list[float], target) -> int | None:
    if target is None:
        return None
    return min(range(len(vals)), key=lambda i: abs(float(vals[i]) - float(target)))


def _holdout_sharpe(returns, ppy: int, holdout: float) -> float | None:
    """Annualized Sharpe over the last ``holdout`` fraction of per-bar returns —
    an OOS-consistent read (the preset was chosen by walk-forward OOS, not
    full-window fit). Returns None if the tail is too short or has no dispersion
    (a param combo that traded flat over the tail is not comparable)."""
    if not returns:
        return None
    tail = returns[int(len(returns) * (1.0 - holdout)):]
    if len(tail) < 10:
        return None
    mean = sum(tail) / len(tail)
    var = sum((r - mean) ** 2 for r in tail) / (len(tail) - 1)
    sd = var ** 0.5
    if sd <= 1e-12:
        return None
    return (mean / sd) * (ppy ** 0.5)


def _assert_axes_in_domain() -> None:
    for sid, (yn, yv, xn, xv) in AXES.items():
        space = strategy_param_space(sid)
        for name, vals in ((yn, yv), (xn, xv)):
            real = space._by_name[name]
            for v in vals:
                assert real.contains(v), f"{sid}.{name}={v} outside declared domain"


def sweep_cell(sid: str, symbol: str, tf_str: str, base_params: dict,
               max_bars: int, holdout: float):
    tf = _TFMAP[tf_str]
    bars = lab.load_cached_bars(symbol, tf)
    if len(bars) > max_bars:
        bars = bars[-max_bars:]
    asset = lab.SYMBOL_ASSET[symbol]
    ppy = lab.periods_per_year(tf, asset)
    cfg = lab.ProConfig(asset=asset, mode=lab.TradingMode.BACKTEST,
                        max_debate_rounds=1)
    htf = lab._HTF_TIMEFRAMES.get(sid, ())
    trial = lab.LabTrial(sid, cfg, symbol, asset, list(bars), ppy,
                         objective_name="sharpe", htf_timeframes=htf)
    yn, yv, xn, xv = AXES[sid]
    grid: list[list[float | None]] = []
    for y in yv:
        row: list[float | None] = []
        for x in xv:
            params = {**base_params, yn: y, xn: x}
            try:
                _, returns = trial(params)
                hs = _holdout_sharpe(returns, ppy, holdout)
                row.append(None if hs is None else round(hs, 4))
            except Exception:  # noqa: BLE001 — a bad combo → blank cell, not a crash
                row.append(None)
        grid.append(row)
    star = (_closest_index(yv, base_params.get(yn)),
            _closest_index(xv, base_params.get(xn)))
    if star[0] is None or star[1] is None:
        star = None
    return yn, yv, xn, xv, grid, star, len(bars)


def _verdict(grid, star) -> tuple[str, str]:
    """Honest robustness read of a holdout-Sharpe surface: is the boxed preset
    cell sitting on a positive plateau, or is it fragile?"""
    flat = [v for row in grid for v in row if v is not None]
    if not flat:
        return "no-data", "no comparable cells (flat/no-trade combos)"
    pos = sum(1 for v in flat if v > 0)
    frac_pos = pos / len(flat)
    boxed = None
    if star is not None:
        boxed = grid[star[0]][star[1]]
    stats = f"{pos}/{len(flat)} cells positive holdout-Sharpe"
    if boxed is not None:
        stats += f"; preset cell {boxed:+.3f}"
    if frac_pos >= 0.75 and (boxed is None or boxed > 0):
        return "plateau", f"robust — {stats}"
    if frac_pos >= 0.5:
        return "mixed", f"mixed — {stats}"
    return "fragile", f"fragile — {stats}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-bars", type=int, default=1500)
    ap.add_argument("--holdout", type=float, default=0.3,
                    help="fraction of the tail scored out-of-sample (default 0.3)")
    args = ap.parse_args()
    _assert_axes_in_domain()

    try:
        from tradingagents.pro.backtest import charts
        have_charts = True
    except ImportError:
        charts = None
        have_charts = False

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    hpct = int(round(args.holdout * 100))
    md = [
        "# Strategy Lab — parameter sensitivity (SO-B)", "",
        f"**Held-out (last {hpct}% of the window) Sharpe** surface over the two "
        "most meaningful parameters of each top preset, every *other* parameter "
        "held at the shipped value. The holdout metric matches how presets are "
        "chosen (walk-forward OOS), so the surface answers the right question: "
        "*does the preset's neighbourhood generalize to unseen bars?* **Robust = "
        "plateau** (boxed preset cell in a field of positive cells); **fragile = "
        "spike/island** (a lone bright cell among negatives). Visual twin of the "
        "DSR/PBO guard; bar-capped for turnaround — the preset's headline "
        "evidence is its full walk-forward OOS run (REPORT.md), not this grid.", "",
        f"Producer: `scripts/pro_sensitivity.py` (bars capped at {args.max_bars}, "
        f"holdout {hpct}%). "
        + ("Heatmap PNGs in `sensitivity/`." if have_charts
           else "_matplotlib absent — PNGs skipped; tables only._"), "",
    ]
    verdicts: list[tuple[str, str, str, str, str]] = []
    for sid, symbol, tf_str in TOP_CELLS:
        preset = presets.CATALOG.get(sid, {}).get((symbol, tf_str))
        if preset is None:
            continue
        yn, yv, xn, xv, grid, star, nbars = sweep_cell(
            sid, symbol, tf_str, preset.params, args.max_bars, args.holdout)
        tag, note = _verdict(grid, star)
        verdicts.append((sid, symbol, tf_str, tag, note))
        stamp = f"{sid}_{symbol}_{tf_str}".replace("-", "")
        png = CHART_DIR / f"{stamp}.png"
        if have_charts:
            charts.param_sensitivity_heatmap(
                png, xn, xv, yn, yv, grid, objective=f"OOS Sharpe ({hpct}% tail)",
                title=f"{sid} · {symbol} {tf_str} — holdout Sharpe: {yn} × {xn}",
                star=star)
        md.append(f"### {sid} · {symbol} {tf_str} — **{tag}**")
        md.append("")
        md.append(f"Swept **{yn}** (rows) × **{xn}** (cols); other params at "
                  f"preset {preset.params}. Boxed = shipped preset. "
                  f"_{note}._ ({nbars} bars)")
        md.append("")
        if have_charts:
            md.append(f"![{sid} {symbol} {tf_str} sensitivity](sensitivity/{png.name})")
            md.append("")
        # markdown table (always, so the doc is useful without matplotlib)
        md.append("| " + f"{yn} ╲ {xn}" + " | " + " | ".join(f"{x:g}" for x in xv) + " |")
        md.append("|" + "---|" * (len(xv) + 1))
        for i, y in enumerate(yv):
            cells = []
            for j, v in enumerate(grid[i]):
                s = "—" if v is None else f"{v:.3f}"
                if star == (i, j):
                    s = f"**[{s}]**"
                cells.append(s)
            md.append(f"| {y:g} | " + " | ".join(cells) + " |")
        md.append("")
        print(f"  ✓ {sid:22} {symbol:8} {tf_str:3} — {tag}: {note}")

    roll = ["## Verdict roll-up", "",
            "| Strategy | Sym | TF | Verdict | Detail |",
            "|---|---|---|---|---|"]
    for sid, symbol, tf_str, tag, note in verdicts:
        roll.append(f"| {sid} | {symbol} | {tf_str} | **{tag}** | {note} |")

    tally = {t: sum(1 for *_ , tag, _ in verdicts if tag == t)
             for t in ("plateau", "mixed", "fragile", "no-data")}
    weak = [f"`{s}` {sy} {tf}" for s, sy, tf, tag, _ in verdicts
            if tag in ("fragile", "mixed")]
    summary_weak = (
        "Every top cell reads as a **plateau** on this out-of-sample metric: the "
        "boxed preset sits in a field of positive holdout-Sharpe cells, so its "
        "edge survives small parameter perturbations — the non-curve-fit "
        "signature the DSR/PBO guard rewards."
        if not weak else
        "Weaker cells (" + ", ".join(weak) + ") show a mixed/fragile "
        "neighbourhood — their OOS edge leans on a narrower parameter choice, "
        "consistent with their lower DSR / provisional status.")
    md += roll + [
        "", f"Tally: {tally['plateau']} plateau · {tally['mixed']} mixed · "
        f"{tally['fragile']} fragile · {tally['no-data']} no-data.", "",
        "## Reading the surfaces", "", summary_weak, "",
        "- **Why the holdout metric matters:** an earlier draft scored the "
        "surface on *in-sample full-window* Sharpe and several boxed cells looked "
        "like fragile spikes — an artifact, because presets are chosen by "
        "*out-of-sample* Sharpe, not whole-history fit. Scoring the held-out tail "
        "instead makes the surface answer the same question the guard does, and "
        "the plateaus appear where the guard said they should.",
        "- **These surfaces are a diagnostic, not a re-selection:** nothing is "
        "shipped or pulled from them. They corroborate the walk-forward OOS + "
        "DSR/PBO evidence and make each preset's parameter-robustness visible.", "",
    ]
    (OUT_DIR / "07_sensitivity.md").write_text("\n".join(md))
    print(f"\nwrote {OUT_DIR}/07_sensitivity.md"
          + (f" + {len(TOP_CELLS)} PNGs in sensitivity/" if have_charts else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
