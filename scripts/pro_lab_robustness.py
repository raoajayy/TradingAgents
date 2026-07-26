"""Strategy-Lab robustness reporting (SO-A) — Monte-Carlo, benchmark (buy-&-hold
alpha/beta), and regime breakdown for every *shipped* preset.

The Strategy Lab (``pro_strategy_lab.py``) proves each preset survives
walk-forward OOS + DSR/PBO. This companion pass adds the three robustness views
the operator's optimization brief asks for, computed from a single full-window
backtest of each preset in ``presets.CATALOG`` on the cached bars:

  * **Monte-Carlo** (``montecarlo.monte_carlo_summary``): bootstrap-resample the
    preset's realized trade P&Ls → p5/p50/p95 final equity, median/p95 drawdown,
    and probability of ending below the starting stake.
  * **Benchmark** (``report.extended_report``): buy-&-hold total return, alpha,
    beta, CAGR, Calmar, recovery factor, risk-of-ruin — the strategy measured
    *against* simply holding the asset over the same window.
  * **Regime breakdown**: each trade is tagged with the market regime *at its
    entry bar* (look-ahead-safe: ``classify_regime`` over the trailing window
    ending at the entry bar — no future bars), then P&L is aggregated per regime
    (trending up/down, ranging, high/low volatility, crisis).

Reuses the analytics modules already in the engine (``montecarlo``, ``report``,
``analytics.features``) — nothing new is modelled. No fabricated numbers: cells
with <2 trades get no Monte-Carlo; regimes with no trades are simply absent.

    python scripts/pro_lab_robustness.py            # all shipped presets
    python scripts/pro_lab_robustness.py --json     # also dump robustness.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# import the sibling lab module by path (scripts/ is not a package) for its
# cache loader + per-symbol asset/ppy/config plumbing — single source of truth.
_SPEC = importlib.util.spec_from_file_location(
    "pro_strategy_lab", REPO_ROOT / "scripts" / "pro_strategy_lab.py")
lab = importlib.util.module_from_spec(_SPEC)
sys.modules["pro_strategy_lab"] = lab
_SPEC.loader.exec_module(lab)

from tradingagents.contracts import Timeframe  # noqa: E402
from tradingagents.pro.analytics.features import classify_regime  # noqa: E402
from tradingagents.pro.backtest import build_strategy, presets  # noqa: E402
from tradingagents.pro.backtest.broker import SimBroker  # noqa: E402
from tradingagents.pro.backtest.data import BarReplay  # noqa: E402
from tradingagents.pro.backtest.engine import BacktestEngine  # noqa: E402
from tradingagents.pro.backtest.montecarlo import monte_carlo_summary  # noqa: E402
from tradingagents.pro.backtest.report import extended_report  # noqa: E402

OUT_DIR = REPO_ROOT / "docs" / "backtests" / "strategy_lab"
_TFMAP = {"1h": Timeframe.H1, "4h": Timeframe.H4, "1d": Timeframe.D1}
REGIME_WINDOW = 60   # trailing bars used to classify the regime at each entry


def _run_full(strategy_id: str, symbol: str, tf: Timeframe, params: dict):
    """One look-ahead-safe full-window backtest of a preset on cached bars.
    Returns (bars, result, ppy) or (bars, None, ppy) if there weren't enough
    bars to run."""
    bars = lab.load_cached_bars(symbol, tf)
    asset = lab.SYMBOL_ASSET[symbol]
    ppy = lab.periods_per_year(tf, asset)
    if len(bars) <= lab.MIN_HISTORY + 2:
        return bars, None, ppy
    cfg = lab.ProConfig(asset=asset, mode=lab.TradingMode.BACKTEST,
                        max_debate_rounds=1)
    htf = lab._HTF_TIMEFRAMES.get(strategy_id, ()) or None
    replay = BarReplay(symbol, asset, bars, window=lab.MIN_HISTORY,
                       precompute_indicators=True)
    engine = BacktestEngine(
        None, cfg, replay, broker=SimBroker(initial_equity=lab.INITIAL_EQUITY),
        memory=None, min_history=lab.MIN_HISTORY, decide_every=1,
        periods_per_year=ppy, strategy=build_strategy(strategy_id, params),
        htf_timeframes=htf)
    return bars, engine.run(), ppy


def _regime_at_entries(bars, trades) -> dict[str, dict]:
    """Group trade P&L by the market regime *at each trade's entry bar*.
    Look-ahead-safe: the regime is classified from the trailing ``REGIME_WINDOW``
    bars ending at (and including) the entry bar — never any later bar."""
    idx_by_start = {b.start: i for i, b in enumerate(bars)}
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        i = idx_by_start.get(t.opened_at)
        if i is None:
            label = "unknown"
        else:
            win = bars[max(0, i - REGIME_WINDOW + 1): i + 1]
            label = classify_regime(win).value if len(win) >= 2 else "unknown"
        buckets[label].append(t.pnl)
    out: dict[str, dict] = {}
    for label, pnls in buckets.items():
        wins = [p for p in pnls if p > 0]
        out[label] = {
            "n_trades": len(pnls),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        }
    return out


def evaluate_preset(strategy_id, symbol, tf_str, preset) -> dict | None:
    tf = _TFMAP[tf_str]
    bars, result, ppy = _run_full(strategy_id, symbol, tf, preset.params)
    if result is None or not result.equity_curve:
        return None
    trades = result.trades
    pnls = [t.pnl for t in trades]

    tail = bars[lab.MIN_HISTORY:]
    n = len(result.equity_curve)
    timestamps = [b.start for b in tail][:n]
    benchmark_closes = [b.close for b in tail][:n]
    years = max((bars[-1].start - bars[lab.MIN_HISTORY].start).days / 365.25,
                1e-9)

    ext = extended_report(result.equity_curve, trades, timestamps,
                          benchmark_closes, lab.INITIAL_EQUITY, years, ppy)
    mc = (monte_carlo_summary(pnls, lab.INITIAL_EQUITY).__dict__
          if len(pnls) >= 2 else None)
    final_equity = result.equity_curve[-1]
    strat_return = final_equity / lab.INITIAL_EQUITY - 1.0
    return {
        "strategy_id": strategy_id, "symbol": symbol, "timeframe": tf_str,
        "bars": len(bars), "n_trades": len(trades),
        "oos_sharpe": preset.oos_sharpe, "deflated_sharpe": preset.deflated_sharpe,
        "total_return": round(strat_return, 4),
        "benchmark_total_return": round(ext.benchmark_total_return, 4),
        "alpha": round(ext.alpha, 4), "beta": round(ext.beta, 4),
        "cagr": round(ext.cagr, 4), "calmar": round(ext.calmar, 4),
        "recovery_factor": round(ext.recovery_factor, 4),
        "risk_of_ruin": round(ext.risk_of_ruin, 4),
        "monte_carlo": mc, "regimes": _regime_at_entries(bars, trades),
    }


# --- report writers ----------------------------------------------------------

def _fmt(x, pct=False):
    if x is None:
        return "—"
    return f"{x * 100:+.1f}%" if pct else f"{x:.3f}"


def write_montecarlo(rows: list[dict]) -> None:
    lines = [
        "# Strategy Lab — Monte-Carlo robustness (SO-A)", "",
        "Bootstrap resampling (1000 paths, seed 7) of each shipped preset's "
        "realized trade P&Ls, starting from $100,000. `prob_loss` is the "
        "fraction of resampled paths ending **below** the starting stake — the "
        "single most useful robustness number: a preset with a real edge but an "
        "unlucky trade order should still rarely lose money across resamples.", "",
        "Source: one full-window backtest per preset on cached bars "
        "(`scripts/pro_lab_robustness.py`). Presets with <2 trades are omitted "
        "(Monte-Carlo undefined).", "",
        "| Strategy | Sym | TF | Trades | p5 equity | p50 equity | p95 equity | "
        "maxDD p50 | maxDD p95 | prob_loss |",
        "|---|---|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        mc = r["monte_carlo"]
        if not mc:
            continue
        lines.append(
            f"| {r['strategy_id']} | {r['symbol']} | {r['timeframe']} | "
            f"{r['n_trades']} | ${mc['final_equity_p5']:,.0f} | "
            f"${mc['final_equity_p50']:,.0f} | ${mc['final_equity_p95']:,.0f} | "
            f"{mc['max_drawdown_p50'] * 100:.1f}% | "
            f"{mc['max_drawdown_p95'] * 100:.1f}% | {mc['prob_loss'] * 100:.1f}% |")
    lines += [
        "", "**Reading it:** a low `prob_loss` with a p5 equity above the stake "
        "is the robust signature; a high `prob_loss` warns that the historical "
        "profit leaned on trade *ordering* (luck), not a repeatable edge. These "
        "are in-sample resamples — they bound trade-sequence risk, not "
        "regime-change risk (see the regime breakdown for that).", ""]
    (OUT_DIR / "04_montecarlo.md").write_text("\n".join(lines))


def write_benchmark(rows: list[dict]) -> None:
    lines = [
        "# Strategy Lab — benchmark vs buy-&-hold (SO-A)", "",
        "Each shipped preset measured **against simply holding the asset** over "
        "the same full window. `alpha`/`beta` are from an OLS fit of the "
        "strategy's per-bar returns on the buy-&-hold returns; `total_return` is "
        "the strategy, `benchmark_total_return` is buy-&-hold. A strategy earns "
        "its complexity only if it beats buy-&-hold on a risk-adjusted basis "
        "(positive alpha, lower drawdown) — not necessarily on raw return.", "",
        "Source: `report.extended_report` over one full-window backtest per "
        "preset (`scripts/pro_lab_robustness.py`).", "",
        "| Strategy | Sym | TF | Strat ret | Buy&Hold | Alpha | Beta | CAGR | "
        "Calmar | Recovery | Risk-of-ruin |",
        "|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['strategy_id']} | {r['symbol']} | {r['timeframe']} | "
            f"{_fmt(r['total_return'], pct=True)} | "
            f"{_fmt(r['benchmark_total_return'], pct=True)} | "
            f"{r['alpha']:+.4f} | {r['beta']:.2f} | {_fmt(r['cagr'], pct=True)} | "
            f"{r['calmar']:.2f} | {r['recovery_factor']:.2f} | "
            f"{r['risk_of_ruin'] * 100:.1f}% |")
    lines += [
        "", "**Reading it:** these presets are deliberately **low-beta, "
        "positive-alpha** — they are in the market a fraction of the time (most "
        "are breakout/trend systems that sit flat between signals), so raw "
        "return is usually *below* buy-&-hold in a roaring bull window, while "
        "alpha and Calmar (return per unit of max drawdown) are the honest edge. "
        "A near-zero beta with positive alpha is exactly the uncorrelated "
        "return stream the portfolio layer (02_portfolio.md) then combines.", ""]
    (OUT_DIR / "05_benchmark.md").write_text("\n".join(lines))


def write_regime(rows: list[dict]) -> None:
    order = ["trending_up", "trending_down", "ranging", "high_volatility",
             "low_volatility", "crisis", "unknown"]
    lines = [
        "# Strategy Lab — regime breakdown (SO-A)", "",
        "Each shipped preset's trades grouped by the **market regime at the "
        "trade's entry bar**. The regime is classified look-ahead-safely by "
        "`analytics.features.classify_regime` over the trailing "
        f"{REGIME_WINDOW} bars ending at (and including) the entry bar — never a "
        "future bar. This answers *\"when does this preset actually make its "
        "money, and where does it bleed?\"* — the input the regime-aware variant "
        "(D) uses to switch components.", "",
        "Regimes: `trending_up`/`trending_down` (strong directional slope + fit), "
        "`ranging` (choppy), `high_volatility`/`low_volatility`, `crisis` "
        "(extreme vol). Per cell: trades (net P&L $) by regime.", "",
    ]
    for r in rows:
        reg = r["regimes"]
        if not reg:
            continue
        lines.append(f"### {r['strategy_id']} · {r['symbol']} {r['timeframe']} "
                     f"({r['n_trades']} trades)")
        lines.append("")
        lines.append("| Regime | Trades | Win rate | Total P&L | Avg P&L |")
        lines.append("|---|--:|--:|--:|--:|")
        for label in order:
            if label not in reg:
                continue
            s = reg[label]
            lines.append(f"| {label} | {s['n_trades']} | "
                         f"{s['win_rate'] * 100:.0f}% | ${s['total_pnl']:,.0f} | "
                         f"${s['avg_pnl']:,.0f} |")
        lines.append("")
    lines += [
        "**Reading it:** trend/breakout presets should earn the bulk of their "
        "P&L in `trending_up`/`trending_down` and give some back in `ranging` — "
        "that concentration is the strategy behaving as designed, not overfit. A "
        "preset whose profit comes only from a single `high_volatility`/`crisis` "
        "cluster is fragile and should be treated with suspicion.", ""]
    (OUT_DIR / "06_regime_breakdown.md").write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="also write robustness.json alongside the docs")
    args = ap.parse_args()

    rows: list[dict] = []
    for strategy_id, cells in sorted(presets.CATALOG.items()):
        for (symbol, tf_str), preset in sorted(cells.items()):
            try:
                row = evaluate_preset(strategy_id, symbol, tf_str, preset)
            except Exception as exc:  # noqa: BLE001 — report, never crash the pass
                print(f"  ! {strategy_id} {symbol} {tf_str}: {exc}")
                continue
            if row is None:
                print(f"  - {strategy_id} {symbol} {tf_str}: no result (thin data)")
                continue
            rows.append(row)
            mc = row["monte_carlo"]
            pl = f"prob_loss {mc['prob_loss'] * 100:.0f}%" if mc else "MC n/a"
            print(f"  ✓ {strategy_id:22} {symbol:8} {tf_str:3} "
                  f"trades={row['n_trades']:>3} alpha={row['alpha']:+.4f} {pl}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_montecarlo(rows)
    write_benchmark(rows)
    write_regime(rows)
    if args.json:
        (OUT_DIR / "robustness.json").write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {OUT_DIR}/04_montecarlo.md, 05_benchmark.md, "
          f"06_regime_breakdown.md ({len(rows)} presets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
