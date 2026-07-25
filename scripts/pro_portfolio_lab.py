"""Strategy Lab — Phase 5: portfolio combination of the preset winners.

The single-strategy presets have modest individual edges. This measures whether
combining the DIVERSIFIED guard-passing daily-crypto winners (different
archetypes — trend / breakout / mean-reversion — on ETH & SOL) into an
equal-weight portfolio lifts the risk-adjusted return, the one genuine
"free lunch" (diversification across uncorrelated positive-expectancy streams).

Each component runs a full backtest at its SHIPPED PRESET params (chosen
out-of-sample by the walk-forward in `presets.py`), on cached bars. Per-bar
equity returns are aligned by timestamp and equal-weighted; the script reports
the blended Sharpe / MAR / max-drawdown, the pairwise correlation matrix, and
the comparison against the best single component — all from the same bars, no
new fitting. Writes docs/backtests/strategy_lab/02_portfolio.md.

    python scripts/pro_portfolio_lab.py
"""

from __future__ import annotations

import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tradingagents.contracts import ProConfig, Timeframe, TradingMode  # noqa: E402
from tradingagents.pro.backtest import (  # noqa: E402
    BarReplay,
    SimBroker,
    build_preset_strategy,
)
from tradingagents.pro.backtest.engine import BacktestEngine  # noqa: E402
from tradingagents.pro.backtest.metrics import (  # noqa: E402
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)
from tradingagents.pro.dashboard.backtest_job import periods_per_year  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from pro_strategy_lab import (  # noqa: E402
    MIN_HISTORY,
    SYMBOL_ASSET,
    load_cached_bars,
)

OUT = REPO_ROOT / "docs" / "backtests" / "strategy_lab" / "02_portfolio.md"
INITIAL_EQUITY = 100_000.0

# The diversified, high-confidence daily-crypto winners (param-stability 1.0):
# three distinct archetypes across ETH + SOL. Equal-weight portfolio.
COMPONENTS = [
    ("trend_following_v2", "ETH-USD", "1d"),
    ("volatility_breakout_v1", "ETH-USD", "1d"),
    ("trend_following_v1", "ETH-USD", "1d"),
    ("mean_reversion_v1", "SOL-USD", "1d"),
    ("trend_following_v2", "SOL-USD", "1d"),
    ("volatility_breakout_v1", "SOL-USD", "1d"),
]


def _run_component(sid: str, symbol: str, tf: Timeframe) -> dict[datetime, float]:
    """Full backtest at the shipped preset params; returns a {bar_start: return}
    map (per-bar equity returns keyed by the bar timestamp for alignment)."""
    bars = load_cached_bars(symbol, tf)
    if not bars:
        raise SystemExit(f"no cached bars for {symbol} {tf.value} — run pro_fetch_bars.py")
    asset = SYMBOL_ASSET[symbol]
    config = ProConfig(asset=asset, mode=TradingMode.BACKTEST, max_debate_rounds=1)
    replay = BarReplay(symbol, asset, bars, window=MIN_HISTORY,
                       precompute_indicators=True)
    engine = BacktestEngine(
        None, config, replay, broker=SimBroker(initial_equity=INITIAL_EQUITY),
        memory=None, min_history=MIN_HISTORY, decide_every=1,
        periods_per_year=periods_per_year(tf, asset),
        strategy=build_preset_strategy(sid, symbol, tf.value))
    result = engine.run()
    # equity curve starts after warm-up; align to the decision bars' timestamps
    tail = bars[MIN_HISTORY:]
    ts = [b.start for b in tail][: len(result.equity_curve)]
    eq = result.equity_curve
    return {ts[i]: (eq[i] / eq[i - 1] - 1.0)
            for i in range(1, min(len(ts), len(eq))) if eq[i - 1] > 0}


def _curve(returns: list[float]) -> list[float]:
    eq, cur = [INITIAL_EQUITY], INITIAL_EQUITY
    for r in returns:
        cur *= (1 + r)
        eq.append(cur)
    return eq


def main() -> int:
    ppy = periods_per_year(Timeframe.D1, SYMBOL_ASSET["ETH-USD"])  # daily crypto = 365
    streams: dict[str, dict[datetime, float]] = {}
    for sid, symbol, tf in COMPONENTS:
        key = f"{sid}@{symbol}/{tf}"
        streams[key] = _run_component(sid, symbol, Timeframe(tf))
        print(f"ran {key}: {len(streams[key])} return-bars", flush=True)

    # union of all timestamps; a component contributes 0 on bars it has no
    # position/return for (flat), so the equal-weight blend is well-defined
    all_ts = sorted({t for s in streams.values() for t in s})
    weight = 1.0 / len(streams)
    blended = [sum(streams[k].get(t, 0.0) for k in streams) * weight for t in all_ts]

    # per-component annualized Sharpe on its own active bars
    comp_rows = []
    for k, s in streams.items():
        r = [s[t] for t in sorted(s)]
        comp_rows.append((k, sharpe_ratio(r, ppy), max_drawdown(_curve(r)), len(r)))
    best_single = max(comp_rows, key=lambda x: x[1])

    blend_sharpe = sharpe_ratio(blended, ppy)
    blend_sortino = sortino_ratio(blended, ppy)
    blend_curve = _curve(blended)
    blend_dd = max_drawdown(blend_curve)
    blend_total = blend_curve[-1] / blend_curve[0] - 1.0

    # pairwise correlation of the aligned (0-filled) component series
    aligned = {k: [s.get(t, 0.0) for t in all_ts] for k, s in streams.items()}
    keys = list(aligned)

    def corr(a, b):
        if len(a) < 2 or statistics.pstdev(a) == 0 or statistics.pstdev(b) == 0:
            return 0.0
        ma, mb = statistics.mean(a), statistics.mean(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=False)) / len(a)
        return cov / (statistics.pstdev(a) * statistics.pstdev(b))

    lines: list[str] = []
    A = lines.append
    A("# Strategy Lab — Portfolio Combination (Phase 5)\n")
    A(f"_Generated {datetime.now(timezone.utc).isoformat()} · equal-weight blend "
      f"of {len(streams)} diversified daily-crypto preset strategies · params "
      f"fixed from the walk-forward presets (no new fitting) · annualization "
      f"{ppy}/yr._\n")
    A("## Blended portfolio vs best single component\n")
    A("| | Ann. Sharpe | Sortino | Max DD | Total return |")
    A("| --- | --- | --- | --- | --- |")
    A(f"| **Equal-weight portfolio** | **{blend_sharpe:.3f}** | {blend_sortino:.3f} "
      f"| {blend_dd:.2%} | {blend_total:+.2%} |")
    A(f"| Best single ({best_single[0]}) | {best_single[1]:.3f} | — "
      f"| {best_single[2]:.2%} | — |")
    A("")
    lift = (blend_sharpe / best_single[1] - 1.0) if best_single[1] > 0 else 0.0
    A(f"Diversification effect: the blend's Sharpe is **{blend_sharpe:.3f}** vs the "
      f"best single component's **{best_single[1]:.3f}** "
      f"({'+' if lift >= 0 else ''}{lift:.0%}).\n")

    A("## Components (own active bars)\n")
    A("| Component | Ann. Sharpe | Max DD | Return-bars |")
    A("| --- | --- | --- | --- |")
    for k, sh, dd, n in sorted(comp_rows, key=lambda x: x[1], reverse=True):
        A(f"| {k} | {sh:.3f} | {dd:.2%} | {n} |")
    A("")

    A("## Pairwise return correlation (aligned, flat-filled)\n")
    A("| | " + " | ".join(f"C{i}" for i in range(len(keys))) + " |")
    A("| --- |" + " --- |" * len(keys))
    for i, ki in enumerate(keys):
        cells = " | ".join(f"{corr(aligned[ki], aligned[kj]):.2f}" for kj in keys)
        A(f"| C{i} {ki} | {cells} |")
    A("")
    avg_corr = statistics.mean(
        corr(aligned[keys[i]], aligned[keys[j]])
        for i in range(len(keys)) for j in range(i + 1, len(keys)))
    A(f"Average pairwise correlation: **{avg_corr:.2f}** (lower = more "
      "diversification benefit).\n")
    A("_Params fixed from the walk-forward-selected presets; this is a portfolio "
      "backtest at those params over the cached daily history, not a new "
      "optimization. Edges are modest and the sample is one crypto regime — treat "
      "as indicative, not a guaranteed forward result._\n")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nblend Sharpe {blend_sharpe:.3f} vs best single {best_single[1]:.3f} "
          f"· avg corr {avg_corr:.2f} · report → {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
