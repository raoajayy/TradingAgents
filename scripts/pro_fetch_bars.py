"""Strategy Lab — Phase 0: probe real vendor history depth + cache bars.

For each symbol × timeframe in the study matrix this pages the vendor backward
(via the same ``fetch_window`` the dashboard uses) until history is exhausted or
``--max-bars`` is reached, records the ACTUAL depth returned, and caches the raw
OHLCV to ``data/bars_cache/<symbol>_<tf>.jsonl`` so every later Strategy-Lab run
is offline + reproducible. Writes ``docs/backtests/strategy_lab/00_data_inventory.md``
(+ a ``00_data_inventory.json`` sidecar) with a per-cell analyzable verdict.

    python scripts/pro_fetch_bars.py                         # full matrix
    python scripts/pro_fetch_bars.py --symbols BTC-USD --timeframes 1h 4h
    python scripts/pro_fetch_bars.py --max-bars 5000         # cap the paging

No numbers are invented: a cell that the vendor can't serve (blocked/geo/empty)
is recorded with its error and marked unavailable — that is itself a finding.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tradingagents.contracts import Timeframe  # noqa: E402
from tradingagents.pro.dashboard.backtest_job import MIN_HISTORY, fetch_window  # noqa: E402
from tradingagents.pro.dashboard.marketdata import MarketDataService  # noqa: E402

CACHE_DIR = REPO_ROOT / "data" / "bars_cache"
OUT_DIR = REPO_ROOT / "docs" / "backtests" / "strategy_lab"

# the four backtestable symbols (DEFAULT_SYMBOLS) × intraday + swing timeframes
DEFAULT_SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "XAUUSD"]
DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]

# A cell is "analyzable" for walk-forward OOS only if it clears the engine
# warm-up plus room for >=2 walk-forward windows at the Lab's smallest window
# (train=80, test=30, embargo=3): 60 + 80 + 3 + 2*30 = 203 bars.
_ANALYZABLE_MIN = MIN_HISTORY + 80 + 3 + 2 * 30


def cache_path(symbol: str, tf: Timeframe) -> Path:
    return CACHE_DIR / f"{symbol}_{tf.value}.jsonl"


def write_cache(path: Path, bars) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for b in bars:
            f.write(json.dumps({
                "t": b.start.isoformat(), "o": b.open, "h": b.high,
                "l": b.low, "c": b.close, "v": b.volume,
            }) + "\n")


def probe_cell(md: MarketDataService, symbol: str, tf: Timeframe,
               max_bars: int) -> dict:
    rec: dict = {
        "symbol": symbol, "timeframe": tf.value, "bars": 0,
        "first": None, "last": None, "truncated": None,
        "analyzable": False, "cache": None, "status": "",
    }
    try:
        bars, truncated = fetch_window(md, symbol, tf, max_bars)
        rec["bars"] = len(bars)
        rec["truncated"] = truncated
        if bars:
            rec["first"] = bars[0].start.isoformat()
            rec["last"] = bars[-1].start.isoformat()
            path = cache_path(symbol, tf)
            write_cache(path, bars)
            rec["cache"] = str(path.relative_to(REPO_ROOT))
        rec["analyzable"] = len(bars) >= _ANALYZABLE_MIN
        rec["status"] = "ok" if rec["analyzable"] else "insufficient-data"
    except Exception as exc:  # noqa: BLE001 — a vendor failure is a finding, not a crash
        rec["status"] = f"unavailable: {type(exc).__name__}: {exc}"[:180]
    return rec


def _md_table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def write_inventory(rows: list[dict], max_bars: int) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "00_data_inventory.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                    "max_bars": max_bars, "analyzable_min_bars": _ANALYZABLE_MIN,
                    "cells": rows}, indent=2), encoding="utf-8")

    n_ok = sum(1 for r in rows if r["analyzable"])
    lines: list[str] = []
    A = lines.append
    A("# Strategy Lab — Data Inventory (Phase 0)\n")
    A(f"_Generated {datetime.now(timezone.utc).isoformat()} · requested up to "
      f"{max_bars:,} bars/cell · a cell is **analyzable** only with "
      f"≥ {_ANALYZABLE_MIN} real bars (engine warm-up {MIN_HISTORY} + ≥2 "
      f"walk-forward windows)._\n")
    A(f"**{n_ok} / {len(rows)} cells are analyzable.** Non-analyzable cells are "
      "recorded as data-limited gaps — no result will be fabricated for them.\n")
    A(_md_table(
        ["Symbol", "TF", "Real bars", "First", "Last", "Truncated", "Verdict"],
        [(r["symbol"], r["timeframe"], r["bars"],
          (r["first"] or "—")[:10], (r["last"] or "—")[:10],
          "yes" if r["truncated"] else ("—" if r["truncated"] is None else "no"),
          "✅ analyzable" if r["analyzable"] else f"⚠️ {r['status']}")
         for r in rows]))
    A("")
    A("## Notes\n")
    A("- Raw bars cached under `data/bars_cache/<symbol>_<tf>.jsonl` "
      "(git-ignored); the Strategy Lab reads the cache, not the vendor, so runs "
      "are offline + reproducible.")
    A("- Depth is whatever the live vendor actually served (Delta/Binance for "
      "crypto, Delta/OANDA/yfinance for gold), paged 1000/request.")
    A("- Re-run this probe to refresh the cache with newer history.")
    (OUT_DIR / "00_data_inventory.md").write_text("\n".join(lines) + "\n",
                                                  encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(prog="pro_fetch_bars")
    ap.add_argument("--max-bars", type=int, default=30_000,
                    help="upper bound on bars to page per cell (default 30000)")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--timeframes", nargs="*", default=DEFAULT_TIMEFRAMES)
    args = ap.parse_args()

    md = MarketDataService()  # lazy, probe-gated vendor registry
    tfs = [Timeframe(t) for t in args.timeframes]
    rows: list[dict] = []
    for symbol in args.symbols:
        for tf in tfs:
            rec = probe_cell(md, symbol, tf, args.max_bars)
            flag = "✅" if rec["analyzable"] else "⚠️"
            print(f"{flag} {symbol:8} {tf.value:4} → {rec['bars']:>7} bars  "
                  f"{rec['status']}", flush=True)
            rows.append(rec)

    write_inventory(rows, args.max_bars)
    n_ok = sum(1 for r in rows if r["analyzable"])
    print(f"\n{n_ok}/{len(rows)} analyzable · inventory → "
          f"{(OUT_DIR / '00_data_inventory.md').relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
