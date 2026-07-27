"""Variant lab (SO-D) — run the four named variants (variants.py) and report
each as a risk-profiled portfolio: OOS Sharpe / MAR / max-drawdown / total
return + a returns-bootstrap Monte-Carlo, with the vol-target leverage disclosed.

Reuses the portfolio-combination machinery (``pro_portfolio_lab._run_component``,
``_curve``): each component preset is run once at its shipped params, per-bar
returns are aligned by timestamp (a component contributes 0 on bars it is flat),
blended by the variant's weight scheme, then scaled to the variant's annualized
volatility target (capped at ``max_leverage`` — the leverage used is reported,
not hidden). No new fitting, no new engine; a variant is a portfolio recipe over
already-guard-passing strategies.

Annualization uses each variant's finest component timeframe (mixed-tf variants
C/D are dominated by 4h bars); this is an approximation disclosed in the report.
OOS Sharpe holds out the last 40% of the timeline — weights are computed on the
first 60% and measured on the tail, so a variant is judged on unseen bars.

    python scripts/pro_variants_lab.py
"""

from __future__ import annotations

import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from pro_portfolio_lab import _curve, _run_component  # noqa: E402
from pro_strategy_lab import SYMBOL_ASSET  # noqa: E402

from tradingagents.contracts import Timeframe  # noqa: E402
from tradingagents.pro.backtest import variants as V  # noqa: E402
from tradingagents.pro.backtest.metrics import (  # noqa: E402
    annualized_return,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)
from tradingagents.pro.dashboard.backtest_job import periods_per_year  # noqa: E402

OUT = REPO_ROOT / "docs" / "backtests" / "strategy_lab" / "08_variants.md"
INITIAL_EQUITY = 100_000.0


def _weights(scheme: str, series: dict[str, list[float]]) -> dict[str, float]:
    if scheme == "equal":
        return {k: 1.0 / len(series) for k in series}
    # inverse-vol (risk parity) — return-agnostic, robust
    inv = {k: 1.0 / (statistics.pstdev(v) or 1e-9) for k, v in series.items()}
    tot = sum(inv.values())
    return {k: inv[k] / tot for k in inv}


def _blend(weights, streams, ts_list):
    return [sum(weights[k] * streams[k].get(t, 0.0) for k in weights)
            for t in ts_list]


def _vol_scale(returns, ppy, target, max_lev):
    """Leverage multiplier to hit the annualized vol target, capped."""
    realized = statistics.pstdev(returns) * (ppy ** 0.5) if len(returns) > 1 else 0.0
    if realized <= 1e-9:
        return 1.0, realized
    return min(max_lev, target / realized), realized


def _returns_mc(returns, ppy, n_paths=1000, seed=7):
    """Returns-bootstrap Monte-Carlo (multiplicative): resample the per-bar
    returns with replacement into ``n_paths`` equity paths → final-equity
    percentiles, median/p95 max-drawdown, and prob_loss."""
    if len(returns) < 2:
        return None
    rng = random.Random(seed)
    n = len(returns)
    finals, dds = [], []
    for _ in range(n_paths):
        sample = [returns[rng.randrange(n)] for _ in range(n)]
        curve = _curve(sample)
        finals.append(curve[-1])
        dds.append(max_drawdown(curve))
    finals.sort()
    dds.sort()

    def pct(xs, p):
        return xs[min(len(xs) - 1, int(p * len(xs)))]

    return {
        "n_paths": n_paths,
        "final_equity_p5": pct(finals, 0.05),
        "final_equity_p50": pct(finals, 0.50),
        "final_equity_p95": pct(finals, 0.95),
        "max_drawdown_p50": pct(dds, 0.50),
        "max_drawdown_p95": pct(dds, 0.95),
        "prob_loss": sum(1 for f in finals if f < INITIAL_EQUITY) / len(finals),
    }


def run_variant(v: V.Variant) -> dict:
    # finest component ppy (mixed-tf variants dominated by the fastest bars)
    ppy = max(periods_per_year(Timeframe(tf), SYMBOL_ASSET[sym])
              for (_, sym, tf) in v.components)
    streams: dict[str, dict[datetime, float]] = {}
    for (sid, sym, tf) in v.components:
        key = f"{sid}@{sym}/{tf}"
        streams[key] = _run_component(sid, sym, Timeframe(tf))
        print(f"    ran {key}: {len(streams[key])} return-bars", flush=True)

    all_ts = sorted({t for s in streams.values() for t in s})
    aligned = {k: [s.get(t, 0.0) for t in all_ts] for k, s in streams.items()}

    # OOS: weights on first 60%, blended returns measured on held-out last 40%
    cut = int(len(all_ts) * 0.6)
    train_ts, test_ts = all_ts[:cut], all_ts[cut:]
    train_series = {k: [streams[k].get(t, 0.0) for t in train_ts] for k in streams}
    w_full = _weights(v.weight_scheme, aligned)
    w_train = _weights(v.weight_scheme, train_series)

    blend_full = _blend(w_full, streams, all_ts)
    scale, realized_vol = _vol_scale(blend_full, ppy, v.vol_target, v.max_leverage)
    scaled_full = [r * scale for r in blend_full]

    # OOS blend uses train-fit weights on the test tail, then the SAME vol scale
    blend_oos = [r * scale for r in _blend(w_train, streams, test_ts)]

    curve = _curve(scaled_full)
    return {
        "variant": v,
        "ppy": ppy,
        "n_bars": len(all_ts),
        "weights": w_full,
        "leverage": scale,
        "realized_vol_unlevered": realized_vol,
        "sharpe_full": sharpe_ratio(scaled_full, ppy),
        "sortino_full": sortino_ratio(scaled_full, ppy),
        "sharpe_oos": sharpe_ratio(blend_oos, ppy),
        "cagr": annualized_return(curve, ppy),
        "max_dd": max_drawdown(curve),
        "total_return": curve[-1] / curve[0] - 1.0,
        "mc": _returns_mc(scaled_full, ppy),
    }


def _mar(cagr, max_dd):
    return cagr / abs(max_dd) if max_dd else float("inf")


def main() -> int:
    V.validate_variants()
    results = []
    for v in V.list_variants():
        print(f"  variant {v.name} ({v.title})…", flush=True)
        results.append(run_variant(v))

    L: list[str] = []
    A = L.append
    A("# Strategy Lab — Named Variants A/B/C/D (SO-D)\n")
    A(f"_Generated {datetime.now(timezone.utc).isoformat()} · each variant is a "
      "portfolio recipe over guard-passing presets (variants.py) · params fixed "
      "from the walk-forward presets, no new fitting · vol-target leverage "
      "disclosed._\n")
    A("Four risk profiles built **only** from strategies that cleared "
      "walk-forward OOS + DSR/PBO. A→C is a deliberate risk ladder; D is "
      "regime-gated. OOS Sharpe holds out the last 40% of the timeline "
      "(weights fit on the first 60%). Annualization uses each variant's finest "
      "component timeframe (mixed-tf variants are 4h-dominated — an "
      "approximation, disclosed).\n")

    A("## Headline\n")
    A("| Variant | Profile | Vol target | Leverage | OOS Sharpe | Full Sharpe | "
      "CAGR | Max DD | MAR | prob_loss |")
    A("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in results:
        v = r["variant"]
        mc = r["mc"]
        pl = f"{mc['prob_loss'] * 100:.0f}%" if mc else "—"
        A(f"| **{v.name} · {v.title}** | {v.risk} | {v.vol_target:.0%} | "
          f"{r['leverage']:.1f}x | {r['sharpe_oos']:.2f} | {r['sharpe_full']:.2f} | "
          f"{r['cagr'] * 100:+.1f}% | {r['max_dd'] * 100:.1f}% | "
          f"{_mar(r['cagr'], r['max_dd']):.2f} | {pl} |")
    A("")
    A("> **Leverage caveat:** the vol target is hit by scaling the unlevered "
      "blend; the `Leverage` column is that multiplier (capped per variant). "
      "Aggressive (C) uses the most — treat its CAGR as leverage-dependent, not "
      "free alpha. All figures are backtest estimates on cached crypto history, "
      "not a promise of live results.\n")

    for r in results:
        v = r["variant"]
        A(f"## {v.name} · {v.title}\n")
        A(v.description + "\n")
        A(f"- **Components ({len(v.components)}, inverse-vol weighted):** "
          + ", ".join(f"{k.split('@')[0]} {k.split('@')[1]} ({r['weights'][k]:.0%})"
                      for k in r["weights"]) + "\n")
        A(f"- **Vol target** {v.vol_target:.0%} → **leverage {r['leverage']:.2f}x** "
          f"(unlevered realized vol {r['realized_vol_unlevered'] * 100:.1f}%, cap "
          f"{v.max_leverage:g}x). Annualization {r['ppy']}/yr over "
          f"{r['n_bars']} blended bars.")
        A(f"- **OOS Sharpe {r['sharpe_oos']:.2f}** · full Sharpe {r['sharpe_full']:.2f} "
          f"· Sortino {r['sortino_full']:.2f} · CAGR {r['cagr'] * 100:+.1f}% · "
          f"max DD {r['max_dd'] * 100:.1f}% · MAR {_mar(r['cagr'], r['max_dd']):.2f} "
          f"· total {r['total_return'] * 100:+.1f}%.")
        mc = r["mc"]
        if mc:
            A(f"- **Monte-Carlo** (1000 returns-bootstrap paths): final equity "
              f"p5 ${mc['final_equity_p5']:,.0f} / p50 "
              f"${mc['final_equity_p50']:,.0f} / p95 ${mc['final_equity_p95']:,.0f}; "
              f"max-DD p50 {mc['max_drawdown_p50'] * 100:.1f}% / p95 "
              f"{mc['max_drawdown_p95'] * 100:.1f}%; **prob_loss "
              f"{mc['prob_loss'] * 100:.0f}%**.")
        if v.notes:
            A(f"- _Note: {v.notes}_")
        A("")

    OUT.write_text("\n".join(L))
    print(f"\nwrote {OUT}")
    for r in results:
        v = r["variant"]
        print(f"  {v.name} {v.title:22} oosSharpe={r['sharpe_oos']:.2f} "
              f"CAGR={r['cagr'] * 100:+.1f}% DD={r['max_dd'] * 100:.1f}% "
              f"lev={r['leverage']:.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
