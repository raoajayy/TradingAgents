"""Strategy test matrix — run EVERY registered strategy over EVERY cached
(symbol, timeframe) cell and capture a functional-health + performance record.

This is the "does every strategy run, and how does it behave" test that backs
STRATEGY_TEST_REPORT.md. For each (strategy, symbol, timeframe) it runs ONE
full-window, look-ahead-safe backtest at the strategy's a-priori DEFAULT params
(the shipped defaults, no preset overlay) and records:

  * status: ok | no-bars | error:<type>
  * n_trades, total_return, sharpe, sortino, max_drawdown, win_rate,
    profit_factor, exposure (traded / total bars)
  * has_preset: whether a tuned preset exists for this exact cell

Default params are used deliberately so the matrix is a clean apples-to-apples
health + baseline-behaviour check across all 10 strategies and 28 cells — the
tuned-preset evidence lives in REPORT.md / robustness.json / 07_sensitivity.md.
Bar-capped for turnaround; serial (no process pool) for determinism.

    python scripts/pro_strategy_test_matrix.py                 # all cells
    python scripts/pro_strategy_test_matrix.py --max-bars 2000
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "pro_strategy_lab", REPO_ROOT / "scripts" / "pro_strategy_lab.py")
lab = importlib.util.module_from_spec(_SPEC)
sys.modules["pro_strategy_lab"] = lab
_SPEC.loader.exec_module(lab)

from tradingagents.contracts import Timeframe  # noqa: E402
from tradingagents.pro.backtest import (  # noqa: E402
    build_strategy,
    list_strategies,
    presets,
)
from tradingagents.pro.backtest.broker import SimBroker  # noqa: E402
from tradingagents.pro.backtest.data import BarReplay  # noqa: E402
from tradingagents.pro.backtest.engine import BacktestEngine  # noqa: E402

OUT_DIR = REPO_ROOT / "docs" / "backtests" / "strategy_lab"
SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "XAUUSD"]
TIMEFRAMES = [Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1,
              Timeframe.H4, Timeframe.D1, Timeframe.W1]
# rules_v1 runs the full deterministic pipeline per bar — far slower than the
# native strategies; cap its bars harder so the matrix finishes.
SLOW_STRATEGIES = {"rules_v1"}


def _metrics(report) -> dict:
    g = lambda n: getattr(report, n, None)  # noqa: E731
    return {
        "n_trades": g("n_trades"), "total_return": g("total_return"),
        "sharpe": g("sharpe"), "sortino": g("sortino"),
        "max_drawdown": g("max_drawdown"), "win_rate": g("win_rate"),
        "profit_factor": g("profit_factor"), "mar": g("mar"),
    }


def run_cell(sid: str, symbol: str, tf: Timeframe, max_bars: int) -> dict:
    row = {"strategy_id": sid, "symbol": symbol, "timeframe": tf.value,
           "status": "ok", "bars": 0, "has_preset": False}
    row["has_preset"] = (presets.CATALOG.get(sid, {})
                         .get((symbol, tf.value)) is not None)
    try:
        bars = lab.load_cached_bars(symbol, tf)
    except Exception as exc:  # noqa: BLE001
        row["status"] = f"error:load:{type(exc).__name__}"
        return row
    cap = min(max_bars, 1200) if sid in SLOW_STRATEGIES else max_bars
    if len(bars) > cap:
        bars = bars[-cap:]
    row["bars"] = len(bars)
    if len(bars) <= lab.MIN_HISTORY + 5:
        row["status"] = "no-bars"
        return row
    asset = lab.SYMBOL_ASSET[symbol]
    ppy = lab.periods_per_year(tf, asset)
    cfg = lab.ProConfig(asset=asset, mode=lab.TradingMode.BACKTEST,
                        max_debate_rounds=1)
    # mirror the dashboard job: only keep HTFs strictly COARSER than the base
    # timeframe (a 1d run can't take a 1d HTF) — else the engine rejects it.
    from tradingagents.pro.backtest.multitf import HTF_SECONDS
    want = lab._HTF_TIMEFRAMES.get(sid, ())
    htf = tuple(t for t in want if HTF_SECONDS[t] > HTF_SECONDS[tf]) or None
    t0 = time.time()
    try:
        replay = BarReplay(symbol, asset, bars, window=lab.MIN_HISTORY,
                           precompute_indicators=True)
        engine = BacktestEngine(
            None, cfg, replay,
            broker=SimBroker(initial_equity=lab.INITIAL_EQUITY),
            memory=None, min_history=lab.MIN_HISTORY, decide_every=1,
            periods_per_year=ppy, strategy=build_strategy(sid, {}),
            htf_timeframes=htf)
        result = engine.run()
    except Exception as exc:  # noqa: BLE001 — a strategy that crashes IS a finding
        row["status"] = f"error:run:{type(exc).__name__}"
        row["error"] = str(exc)[:200]
        return row
    row.update(_metrics(result.report))
    n = len(result.equity_curve)
    row["exposure"] = round(n / max(len(bars), 1), 3)
    row["secs"] = round(time.time() - t0, 2)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-bars", type=int, default=3000)
    args = ap.parse_args()

    strategies = [s.id for s in list_strategies()]
    rows: list[dict] = []
    t0 = time.time()
    for sid in strategies:
        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                row = run_cell(sid, symbol, tf, args.max_bars)
                rows.append(row)
                flag = ("·" if row["status"] == "ok"
                        else ("∅" if row["status"] == "no-bars" else "✗"))
                nt = row.get("n_trades")
                extra = (f"trades={nt:>4} ret={row.get('total_return') or 0:+.3f} "
                         f"sharpe={row.get('sharpe') or 0:+.2f}"
                         if row["status"] == "ok" else row["status"])
                star = " *preset" if row["has_preset"] else ""
                print(f"  {flag} {sid:22} {symbol:8} {tf.value:4} {extra}{star}")
    dt = time.time() - t0

    ok = [r for r in rows if r["status"] == "ok"]
    errs = [r for r in rows if r["status"].startswith("error")]
    nobars = [r for r in rows if r["status"] == "no-bars"]
    traded = [r for r in ok if (r.get("n_trades") or 0) > 0]
    summary = {
        "strategies": len(strategies), "symbols": len(SYMBOLS),
        "timeframes": len(TIMEFRAMES), "cells": len(rows),
        "ok": len(ok), "errors": len(errs), "no_bars": len(nobars),
        "traded": len(traded), "presets": sum(1 for r in rows if r["has_preset"]),
        "elapsed_secs": round(dt, 1),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "test_matrix.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2))
    print("\n" + json.dumps(summary, indent=2))
    if errs:
        print("\nERRORS:")
        for r in errs:
            print(f"  ✗ {r['strategy_id']} {r['symbol']} {r['timeframe']}: "
                  f"{r['status']} {r.get('error', '')}")
    print(f"\nwrote {OUT_DIR}/test_matrix.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
