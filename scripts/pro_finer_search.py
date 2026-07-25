"""Strategy Lab — Phase 6: finer walk-forward search on the top cells.

The main sweep used coarse categorical grids (6-9 points) for tractability. This
runs a **genetic** walk-forward search over each strategy's FULL declared
parameter ranges on the highest-value cells, to see whether a finer search
sharpens the out-of-sample edge beyond the shipped presets — and, crucially,
whether it survives the same DSR/PBO guard bar (a finer search tries more
configs, so the deflated Sharpe penalty is larger; a gain must beat that).

For each cell: genetic walk-forward (OOS Sharpe) over the full space, then a
guarded full-window genetic fit for DSR/PBO/verdict. Prints a comparison to the
shipped preset so we only promote params that are BOTH better OOS and guard-
passing. Writes docs/backtests/strategy_lab/03_finer_search.md.

    python scripts/pro_finer_search.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from pro_strategy_lab import (  # noqa: E402
    _HTF_TIMEFRAMES,
    SYMBOL_ASSET,
    LabTrial,
    load_cached_bars,
    window_plan,
)

from tradingagents.contracts import ProConfig, Timeframe, TradingMode  # noqa: E402
from tradingagents.pro.backtest import run_optimization, run_walk_forward_optimization  # noqa: E402
from tradingagents.pro.backtest.presets import get_preset  # noqa: E402
from tradingagents.pro.backtest.registry import strategy_param_space  # noqa: E402
from tradingagents.pro.dashboard.backtest_job import periods_per_year  # noqa: E402

OUT = REPO_ROOT / "docs" / "backtests" / "strategy_lab" / "03_finer_search.md"
MAX_BARS = 4000

# the top guard-passing cells worth a deeper look (the portfolio components +
# the strongest other winners)
TOP_CELLS = [
    ("trend_following_v2", "ETH-USD", "1d"),
    ("trend_following_v1", "ETH-USD", "1d"),
    ("volatility_breakout_v1", "ETH-USD", "1d"),
    ("regime_momentum_v1", "ETH-USD", "4h"),
    ("trend_following_v2", "ETH-USD", "4h"),
    ("volatility_breakout_v1", "ETH-USD", "4h"),
    ("trend_following_v2", "SOL-USD", "1d"),
    ("volatility_breakout_v1", "SOL-USD", "1d"),
    ("mean_reversion_v1", "SOL-USD", "1d"),
]

# genetic budget: population × generations evaluated per train window
POP, GENS, SEED = 12, 5, 1


def evaluate(sid: str, symbol: str, tf: Timeframe) -> dict:
    bars = load_cached_bars(symbol, tf)
    if bars and len(bars) > MAX_BARS:
        bars = bars[-MAX_BARS:]
    if not bars:
        return {"status": "no-bars"}
    asset = SYMBOL_ASSET[symbol]
    ppy = periods_per_year(tf, asset)
    config = ProConfig(asset=asset, mode=TradingMode.BACKTEST, max_debate_rounds=1)
    space = strategy_param_space(sid)                     # FULL declared ranges
    htf = _HTF_TIMEFRAMES.get(sid, ())
    plan = window_plan(len(bars))
    if plan is None:
        return {"status": "insufficient-data", "bars": len(bars)}
    train, test, step, embargo = plan

    def factory(sl):
        return LabTrial(sid, config, symbol, asset, list(sl), ppy,
                        objective_name="sharpe", htf_timeframes=htf)

    # walk-forward's genetic search uses the sampler's default budget (it does
    # not forward search_config); the guarded full-window fit below sets it.
    wf = run_walk_forward_optimization(
        space, bars, factory, train=train, test=test, step=step, embargo=embargo,
        search="genetic", seed=SEED, objective_name="sharpe", n_trials=None)
    s = wf.summary()
    full = run_optimization(
        space, LabTrial(sid, config, symbol, asset, list(bars), ppy,
                        objective_name="sharpe", htf_timeframes=htf),
        search="genetic", seed=SEED, objective_name="sharpe", max_workers=1,
        search_config={"population": POP, "generations": GENS})
    passes = (full.deflated_sharpe is not None and full.pbo is not None
              and full.deflated_sharpe >= 0.6 and full.pbo <= 0.5
              and (s.get("oos_sharpe") or 0) > 0)
    return {
        "status": "ok", "oos_sharpe": s.get("oos_sharpe"),
        "params": s.get("most_common_params"), "share": s.get("most_common_share"),
        "n_trials": full.n_trials, "dsr": full.deflated_sharpe, "pbo": full.pbo,
        "passes": passes, "verdict": full.verdict(),
    }


def main() -> int:
    lines: list[str] = []
    A = lines.append
    A("# Strategy Lab — Finer Walk-Forward Search (Phase 6)\n")
    A(f"_Generated {datetime.now(timezone.utc).isoformat()} · genetic search "
      f"(pop {POP} × {GENS} gens) over the FULL declared param ranges on the top "
      f"cells vs the coarse-grid presets. A finer search evaluates more configs, "
      f"so DSR is deflated harder — a promotion must beat both the preset OOS "
      f"Sharpe AND the guard bar._\n")
    A("| Cell | preset OOS | finer OOS | finer DSR | finer PBO | guard | verdict |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    promotions = []
    for sid, symbol, tf in TOP_CELLS:
        r = evaluate(sid, symbol, Timeframe(tf))
        preset = get_preset(sid, symbol, tf)
        p_oos = f"{preset.oos_sharpe:.3f}" if preset else "—"
        if r["status"] != "ok":
            A(f"| {sid}@{symbol}/{tf} | {p_oos} | {r['status']} | — | — | — | — |")
            print(f"{sid}@{symbol}/{tf}: {r['status']}", flush=True)
            continue
        better = preset is not None and (r["oos_sharpe"] or 0) > preset.oos_sharpe
        promote = r["passes"] and better
        if promote:
            promotions.append((sid, symbol, tf, r))
        A(f"| {sid}@{symbol}/{tf} | {p_oos} | {r['oos_sharpe']:.3f} | "
          f"{r['dsr']:.3f} | {r['pbo']:.2f} | {'✅' if r['passes'] else '—'} | "
          f"{'PROMOTE' if promote else ('better,not-robust' if better else 'no gain')} |")
        print(f"{sid}@{symbol}/{tf}: finer_oos={r['oos_sharpe']:.3f} "
              f"dsr={r['dsr']:.3f} pbo={r['pbo']:.2f} preset_oos={p_oos} "
              f"{'PROMOTE' if promote else ''}", flush=True)
    A("")
    if promotions:
        A("## Candidate promotions (better OOS AND guard-passing)\n")
        for sid, symbol, tf, r in promotions:
            A(f"- **{sid}@{symbol}/{tf}** → OOS {r['oos_sharpe']:.3f} "
              f"(DSR {r['dsr']:.3f}, PBO {r['pbo']:.2f}, share {r['share']}): "
              f"`{r['params']}`")
    else:
        A("## No promotions\n")
        A("The finer genetic search did not produce a config that is BOTH better "
          "out-of-sample AND guard-passing than the shipped coarse-grid preset — "
          "evidence the presets are already near the robust optimum for these "
          "cells, and that a finer search mostly buys overfitting (deflated by "
          "DSR). Presets unchanged.")
    A("")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{len(promotions)} promotion(s) · report → {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
