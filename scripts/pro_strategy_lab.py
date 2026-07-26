"""Strategy Lab — Phases 1-2: walk-forward OOS evaluation of every strategy.

Reads the cached bars from `scripts/pro_fetch_bars.py` and, for each analyzable
(strategy × symbol × timeframe) cell, runs:

  1. **Walk-forward OOS** (`run_walk_forward_optimization`) over a coarse,
     in-domain grid → the honest headline: OOS Sharpe concatenated across test
     windows, plus parameter stability (`most_common_params`, `distinct_param_sets`).
  2. **Guarded full-window fit** (`run_optimization` + DSR/PBO) → `deflated_sharpe`,
     `pbo`, `verdict()` with an honestly-disclosed `n_trials`.

Then writes `docs/backtests/strategy_lab/{REPORT.md,results.json,results.csv}`.

    python scripts/pro_strategy_lab.py --self-test          # deterministic, offline, synthetic
    python scripts/pro_strategy_lab.py                       # full cached matrix
    python scripts/pro_strategy_lab.py --strategies trend_following_v1 --symbols BTC-USD

No fabricated numbers: a cell that can't fit ≥2 warm-up-clearing walk-forward
windows is recorded as `insufficient-data`, never scored.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tradingagents.contracts import (  # noqa: E402
    DEFAULT_SYMBOLS,
    AssetClass,
    OHLCVBar,
    ProConfig,
    Timeframe,
    TradingMode,
)
from tradingagents.pro.backtest import (  # noqa: E402
    Param,
    ParamSpace,
    run_optimization,
    run_walk_forward_optimization,
)
from tradingagents.pro.backtest.metrics import equity_returns  # noqa: E402
from tradingagents.pro.backtest.walkforward import walk_forward_opt_windows  # noqa: E402
from tradingagents.pro.dashboard.backtest_job import periods_per_year  # noqa: E402

CACHE_DIR = REPO_ROOT / "data" / "bars_cache"
OUT_DIR = REPO_ROOT / "docs" / "backtests" / "strategy_lab"

SYMBOL_ASSET = {sym: asset for asset, sym in DEFAULT_SYMBOLS.items()}
MIN_HISTORY = 60          # engine warm-up reserve inside every walk-forward slice
INITIAL_EQUITY = 100_000.0

# --- coarse, in-domain lab grids ---------------------------------------------
# Each grid enumerates a curated subset of the strategy's DECLARED param domain
# (validated by build_strategy against the registered ParamSpace). Categorical
# params let us enumerate an exact value set; only the highest-impact knobs are
# swept so the walk-forward stays tractable. Unlisted params keep their a-priori
# defaults. Grids stay within the a-priori-declared ranges (12_ policy).
def _cat(name: str, *values):
    return Param(name, "categorical", choices=tuple(values), default=values[0])


LAB_GRIDS: dict[str, ParamSpace] = {
    "trend_following_v1": ParamSpace(          # 12 — +trail_mode (SO-C1)
        _cat("donchian_period", 20, 50),
        _cat("stop_atr_mult", 2.0, 3.0),
        _cat("trail_mode", "pct", "atr", "chandelier")),
    "trend_following_v2": ParamSpace(          # 12 — +trail_mode (SO-C1)
        _cat("donchian_period", 20, 40),
        _cat("max_adds", 0, 2),
        _cat("trail_mode", "pct", "atr", "chandelier")),
    "mean_reversion_v1": ParamSpace(           # 8
        _cat("lookback", 20, 30),
        _cat("entry_std", 2.0, 2.5),
        _cat("stop_atr_mult", 2.0, 3.0)),
    "momentum_v1": ParamSpace(                 # 9
        _cat("roc_period", 10, 14, 20),
        _cat("roc_threshold", 3.0, 5.0, 8.0)),
    "momentum_v2": ParamSpace(                 # 9 — vol-relative (self-scaling)
        _cat("roc_period", 10, 14, 20),
        _cat("entry_sigma", 1.5, 2.0, 3.0)),
    "htf_momentum_v1": ParamSpace(             # 9
        _cat("roc_period", 10, 14, 20),
        _cat("roc_threshold", 3.0, 4.0, 6.0)),
    "htf_momentum_v2": ParamSpace(             # 9 — HTF alignment as size scaler (SO-C3)
        _cat("roc_period", 10, 14, 20),
        _cat("roc_threshold", 3.0, 4.0, 6.0)),
    "regime_momentum_v1": ParamSpace(          # 8
        _cat("roc_period", 10, 20),
        _cat("roc_threshold", 3.0, 5.0),
        _cat("regime_gate", "on", "off")),
    "ma_crossover_v1": ParamSpace(             # 12 — +adx_filter (SO-C2)
        _cat("fast_period", 8, 20),
        _cat("slow_period", 30, 50, 100),
        _cat("adx_filter", "off", "on")),
    "volatility_breakout_v1": ParamSpace(      # 12 — +trail_mode (SO-C1)
        _cat("lookback", 20, 30),
        _cat("squeeze_pct", 0.03, 0.05, 0.08),
        _cat("trail_mode", "pct", "chandelier")),
    "rules_v1": ParamSpace(                    # 9
        _cat("tp_ladder", "0.5/3.5", "1.0/3.0", "1.5/3.0"),
        _cat("min_risk_reward", 1.5, 1.8, 2.2)),
}
# strategies that consult higher-timeframe context (need an HTF-aware trial)
_HTF_TIMEFRAMES = {"htf_momentum_v1": (Timeframe.D1, Timeframe.W1)}


@dataclass
class LabTrial:
    """Picklable ``(params) -> (objective, per-bar returns)`` for one bar slice.

    Mirrors ``EngineTrial`` but also wires higher-timeframe context when the
    strategy declares it (so ``htf_momentum_v1`` actually confirms against D1/W1
    on intraday runs instead of trading blind)."""

    strategy_id: str
    config: ProConfig
    symbol: str
    asset: AssetClass
    bars: list
    periods_per_year: int
    min_history: int = MIN_HISTORY
    objective_name: str = "sharpe"
    htf_timeframes: tuple = ()

    def __call__(self, params: dict) -> tuple[float, list[float]]:
        from tradingagents.pro.backtest import build_strategy
        from tradingagents.pro.backtest.broker import SimBroker
        from tradingagents.pro.backtest.data import BarReplay
        from tradingagents.pro.backtest.engine import BacktestEngine

        # HTF confirmation only engages when a coarser frame than the run TF
        # exists in the slice; on daily/weekly runs D1/W1 aren't coarser (inert).
        htf = tuple(t for t in self.htf_timeframes) or None
        replay = BarReplay(self.symbol, self.asset, self.bars,
                           window=self.min_history, precompute_indicators=True)
        engine = BacktestEngine(
            None, self.config, replay,
            broker=SimBroker(initial_equity=INITIAL_EQUITY),
            memory=None, min_history=self.min_history, decide_every=1,
            periods_per_year=self.periods_per_year,
            strategy=build_strategy(self.strategy_id, params),
            htf_timeframes=htf)
        result = engine.run()
        objective = getattr(result.report, self.objective_name, None)
        return float(objective or 0.0), equity_returns(result.equity_curve)


@dataclass
class CellResult:
    strategy_id: str
    symbol: str
    timeframe: str
    bars: int
    status: str                       # ok | insufficient-data | error:...
    windows: int = 0
    n_trials: int = 0
    oos_sharpe: float | None = None
    mean_oos_objective: float | None = None
    worst_oos_objective: float | None = None
    profitable_windows: int | None = None
    distinct_param_sets: int | None = None
    most_common_params: dict = field(default_factory=dict)
    most_common_share: float | None = None
    is_best_objective: float | None = None
    deflated_sharpe: float | None = None
    pbo: float | None = None
    verdict: str = ""
    passes_guard: bool = False
    note: str = ""


def load_cached_bars(symbol: str, tf: Timeframe) -> list[OHLCVBar]:
    path = CACHE_DIR / f"{symbol}_{tf.value}.jsonl"
    if not path.exists():
        return []
    bars: list[OHLCVBar] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            bars.append(OHLCVBar(
                timeframe=tf, start=datetime.fromisoformat(d["t"]),
                open=d["o"], high=d["h"], low=d["l"], close=d["c"], volume=d["v"]))
    return bars


def window_plan(n: int) -> tuple[int, int, int, int] | None:
    """Derive (train, test, step, embargo) so each slice clears the warm-up and
    at least two walk-forward windows fit. Returns None if too little data."""
    test = min(max(n // 5, 120), 500)     # OOS slice: > warm-up, enough for trades
    train = min(max(n // 2, 200), 1500)
    embargo = 5
    step = test
    if len(walk_forward_opt_windows(n, train, test, step, embargo)) < 2:
        return None
    return train, test, step, embargo


def evaluate_cell(strategy_id: str, symbol: str, tf: Timeframe,
                  bars: list[OHLCVBar], objective: str, max_workers: int) -> CellResult:
    res = CellResult(strategy_id=strategy_id, symbol=symbol, timeframe=tf.value,
                     bars=len(bars), status="ok")
    asset = SYMBOL_ASSET[symbol]
    ppy = periods_per_year(tf, asset)
    config = ProConfig(asset=asset, mode=TradingMode.BACKTEST, max_debate_rounds=1)
    grid = LAB_GRIDS[strategy_id]
    htf = _HTF_TIMEFRAMES.get(strategy_id, ())

    plan = window_plan(len(bars))
    if plan is None:
        res.status = "insufficient-data"
        res.note = (f"only {len(bars)} bars — cannot fit ≥2 warm-up-clearing "
                    "walk-forward windows; not scored")
        return res
    train, test, step, embargo = plan

    def factory(bar_slice):
        return LabTrial(strategy_id, config, symbol, asset, list(bar_slice), ppy,
                        objective_name=objective, htf_timeframes=htf)

    # 1) walk-forward OOS — the honest headline
    wf = run_walk_forward_optimization(
        grid, bars, factory, train=train, test=test, step=step, embargo=embargo,
        search="grid", objective_name=objective)
    s = wf.summary()
    res.windows = s["windows"]
    res.oos_sharpe = s.get("oos_sharpe")
    res.mean_oos_objective = s.get("mean_oos_objective")
    res.worst_oos_objective = s.get("worst_oos_objective")
    res.profitable_windows = s.get("profitable_windows")
    res.distinct_param_sets = s.get("distinct_param_sets")
    res.most_common_params = s.get("most_common_params", {})
    res.most_common_share = s.get("most_common_share")

    # 2) guarded full-window fit — DSR / PBO / verdict with honest n_trials
    full = run_optimization(
        grid, LabTrial(strategy_id, config, symbol, asset, list(bars), ppy,
                       objective_name=objective, htf_timeframes=htf),
        search="grid", objective_name=objective, max_workers=max_workers)
    res.n_trials = full.n_trials
    res.is_best_objective = full.best_objective
    res.deflated_sharpe = full.deflated_sharpe
    res.pbo = full.pbo
    res.verdict = full.verdict()
    res.passes_guard = (
        full.deflated_sharpe is not None and full.pbo is not None
        and full.deflated_sharpe >= 0.6 and full.pbo <= 0.5
        and (res.oos_sharpe or 0) > 0)
    return res


# --- reporting ---------------------------------------------------------------

def _fmt(v, spec="") -> str:
    if v is None:
        return "—"
    return format(v, spec) if spec else str(v)


def write_reports(results: list[CellResult], objective: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = [r.__dict__ for r in results]
    (OUT_DIR / "results.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                    "objective": objective, "cells": rows}, indent=2),
        encoding="utf-8")

    cols = ["strategy_id", "symbol", "timeframe", "bars", "status", "windows",
            "n_trials", "oos_sharpe", "mean_oos_objective", "worst_oos_objective",
            "profitable_windows", "distinct_param_sets", "most_common_share",
            "deflated_sharpe", "pbo", "passes_guard", "verdict"]
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(
            f'"{r.get(c)}"' if c == "verdict" else str(r.get(c) if r.get(c) is not None else "")
            for c in cols))
    (OUT_DIR / "results.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    scored = [r for r in results if r.status == "ok"]
    passing = [r for r in scored if r.passes_guard]
    md: list[str] = []
    A = md.append
    A("# Strategy Lab — Findings (Phases 1-2)\n")
    A(f"_Generated {datetime.now(timezone.utc).isoformat()} · objective "
      f"`{objective}` · walk-forward OOS headline + DSR/PBO guard bar "
      f"(pass = DSR≥0.6 & PBO≤0.5 & OOS-Sharpe>0)._\n")
    A(f"**{len(passing)} / {len(scored)} scored cells pass the guard bar** "
      f"({len(results) - len(scored)} cells were data-limited and not scored).\n")

    A("## Leaderboard — guard-passing cells (by OOS Sharpe)\n")
    top = sorted(passing, key=lambda r: r.oos_sharpe or -9, reverse=True)
    if top:
        A("| Strategy | Symbol | TF | OOS Sharpe | DSR | PBO | Trials | Stable params |")
        A("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in top:
            A(f"| {r.strategy_id} | {r.symbol} | {r.timeframe} | "
              f"{_fmt(r.oos_sharpe, '.3f')} | {_fmt(r.deflated_sharpe, '.3f')} | "
              f"{_fmt(r.pbo, '.2f')} | {r.n_trials} | "
              f"`{json.dumps(r.most_common_params)}` |")
    else:
        A("_No cell passed the guard bar on the available data._")
    A("")

    A("## All scored cells\n")
    A("| Strategy | Symbol | TF | Bars | Wins | OOS Sharpe | DSR | PBO | Guard | Verdict |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in sorted(scored, key=lambda r: (r.strategy_id, r.symbol, r.timeframe)):
        A(f"| {r.strategy_id} | {r.symbol} | {r.timeframe} | {r.bars} | "
          f"{r.windows} | {_fmt(r.oos_sharpe, '.3f')} | "
          f"{_fmt(r.deflated_sharpe, '.3f')} | {_fmt(r.pbo, '.2f')} | "
          f"{'✅' if r.passes_guard else '—'} | {r.verdict[:60]} |")
    A("")

    limited = [r for r in results if r.status != "ok"]
    if limited:
        A("## Data-limited / skipped cells (no result fabricated)\n")
        A("| Strategy | Symbol | TF | Bars | Status |")
        A("| --- | --- | --- | --- | --- |")
        for r in sorted(limited, key=lambda r: (r.symbol, r.timeframe, r.strategy_id)):
            A(f"| {r.strategy_id} | {r.symbol} | {r.timeframe} | {r.bars} | "
              f"{r.status} |")
        A("")
    A("_Full per-cell metrics + chosen params in `results.json`._\n")
    (OUT_DIR / "REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")


# --- self-test (offline, synthetic) ------------------------------------------

def self_test() -> int:
    """Deterministic single-cell run on synthetic bars — no vendor, no cache.
    Proves the harness plumbing end-to-end for CI."""
    import random

    from tests.pro_fakes import BASE_TS

    random.seed(11)
    bars: list[OHLCVBar] = []
    price = 1000.0
    from datetime import timedelta
    for i in range(700):
        drift = 5.0 if (i // 80) % 2 == 0 else -3.5
        price = max(50.0, price + drift + random.uniform(-7, 7))
        o = price
        c = price + random.uniform(-4, 4)
        h = max(o, c) + abs(random.uniform(0, 8))
        low = max(0.1, min(o, c) - abs(random.uniform(0, 8)))
        bars.append(OHLCVBar(timeframe=Timeframe.H1, start=BASE_TS + timedelta(hours=i),
                             open=o, high=h, low=low, close=c, volume=1000.0))
    r = evaluate_cell("trend_following_v1", "BTC-USD", Timeframe.H1, bars,
                      objective="sharpe", max_workers=1)
    print(f"self-test: status={r.status} windows={r.windows} n_trials={r.n_trials} "
          f"oos_sharpe={_fmt(r.oos_sharpe, '.3f')} dsr={_fmt(r.deflated_sharpe, '.3f')} "
          f"pbo={_fmt(r.pbo, '.2f')} verdict={r.verdict!r}")
    assert r.status == "ok", r.status
    assert r.windows >= 2 and r.n_trials == 12  # 2×2×3 grid
    assert r.most_common_params, "walk-forward should choose params"
    print("self-test OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="pro_strategy_lab")
    ap.add_argument("--self-test", action="store_true",
                    help="deterministic synthetic single-cell run (offline)")
    ap.add_argument("--strategies", nargs="*", default=list(LAB_GRIDS))
    ap.add_argument("--symbols", nargs="*", default=list(SYMBOL_ASSET))
    ap.add_argument("--timeframes", nargs="*",
                    default=["5m", "15m", "30m", "1h", "4h", "1d", "1w"])
    ap.add_argument("--objective", default="sharpe")
    ap.add_argument("--max-workers", type=int, default=1,
                    help="guarded-run parallelism; 1 (default) avoids the "
                         "ProcessPool worker-deadlock seen on long sweeps and "
                         "is barely slower (walk-forward dominates, is serial)")
    ap.add_argument("--max-bars-per-cell", type=int, default=6000,
                    help="use only the most-recent N bars per cell (bounds "
                         "walk-forward compute while keeping ~8 OOS folds)")
    ap.add_argument("--out-dir", default=None,
                    help="write reports to this dir (used for per-symbol shards "
                         "run in parallel, then merged)")
    ap.add_argument("--merge", action="store_true",
                    help="merge docs/backtests/strategy_lab/shard_*/results.json "
                         "into one combined report set")
    args = ap.parse_args()

    global OUT_DIR
    if args.out_dir:
        OUT_DIR = Path(args.out_dir)
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.self_test:
        return self_test()

    if args.merge:
        import glob
        cells: list[dict] = []
        for f in sorted(glob.glob(str(OUT_DIR / "shard_*" / "results.json"))):
            cells += json.loads(Path(f).read_text(encoding="utf-8"))["cells"]
        results = [CellResult(**c) for c in cells]
        results.sort(key=lambda r: (r.strategy_id, r.symbol, r.timeframe))
        write_reports(results, args.objective)
        n_pass = sum(1 for r in results if r.passes_guard)
        print(f"merged {len(results)} cells from shards · {n_pass} pass · "
              f"report → {OUT_DIR / 'REPORT.md'}")
        return 0

    tfs = [Timeframe(t) for t in args.timeframes]
    results: list[CellResult] = []
    for symbol in args.symbols:
        for tf in tfs:
            bars = load_cached_bars(symbol, tf)
            if not bars:
                continue  # no cache for this cell (probe didn't reach it)
            if len(bars) > args.max_bars_per_cell:
                bars = bars[-args.max_bars_per_cell:]  # most-recent window
            for sid in args.strategies:
                try:
                    r = evaluate_cell(sid, symbol, tf, bars, args.objective,
                                      args.max_workers)
                except Exception as exc:  # noqa: BLE001 — one bad cell must not kill the sweep
                    r = CellResult(sid, symbol, tf.value, len(bars),
                                   status=f"error: {type(exc).__name__}: {exc}"[:160])
                flag = "✅" if r.passes_guard else ("·" if r.status == "ok" else "⚠️")
                print(f"{flag} {sid:22} {symbol:8} {tf.value:4} → "
                      f"oos_sharpe={_fmt(r.oos_sharpe, '.3f')} "
                      f"dsr={_fmt(r.deflated_sharpe, '.3f')} pbo={_fmt(r.pbo, '.2f')} "
                      f"[{r.status}]", flush=True)
                results.append(r)
            # checkpoint after every cell so a stall never loses progress and
            # partial results/reports are always inspectable
            if results:
                write_reports(results, args.objective)

    if results:
        write_reports(results, args.objective)
        n_pass = sum(1 for r in results if r.passes_guard)
        print(f"\n{n_pass}/{len(results)} cells pass · report → {OUT_DIR / 'REPORT.md'}")
    else:
        print("no cached bars found — run scripts/pro_fetch_bars.py first")
    return 0


if __name__ == "__main__":
    sys.exit(main())
