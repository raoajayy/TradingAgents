# 17 — Strategy Lab: walk-forward evaluation & tuned presets

## Purpose

Systematically test every built-in strategy across real markets and timeframes,
find where each has an edge and where it breaks down, and ship the parameter
sets that are **robust out-of-sample** (not curve-fit to one window) as an
additive preset catalog. "Best result" is defined as risk-adjusted and
OOS-validated — a strategy is only "top class" if its edge survives walk-forward
testing and the overfitting guards, per `12_validation_methodology.md`.

This is a reproducible, offline program built entirely from existing engine
primitives (no engine changes):

- `scripts/pro_fetch_bars.py` — Phase 0 data probe + bar cache.
- `scripts/pro_strategy_lab.py` — Phase 1-2 walk-forward evaluation + reports.
- `tradingagents/pro/backtest/presets.py` — Phase 3 additive tuned-preset catalog.

## Methodology

For each analyzable **(strategy × symbol × timeframe)** cell:

1. **Walk-forward out-of-sample** (`run_walk_forward_optimization`): a coarse,
   in-domain grid is fit on each rolling *train* window and scored on the next
   *embargoed test* window. The headline is **`oos_sharpe`** — the Sharpe of
   returns concatenated across all test windows (never the in-sample fit) —
   plus parameter stability (`distinct_param_sets`, `most_common_params`,
   `most_common_share`).
2. **Overfitting guards** (`run_optimization` + `validation.py`): a full-window
   grid fit attaches the **deflated Sharpe (DSR)** and **probability of backtest
   overfitting (PBO/CSCV)** with an honestly-disclosed `n_trials`.

**Guard bar (a cell "passes"):** `DSR ≥ 0.6` **and** `PBO ≤ 0.5` **and**
`oos_sharpe > 0`. Only passing cells earn a preset.

**Window sizing** clears the engine warm-up (`min_history = 60`) inside every
slice and requires ≥ 2 walk-forward windows, else the cell is recorded as
`insufficient-data` and **never scored** (no fabricated numbers — null ≠ 0).

**A-priori-range policy** (`12_` Part 3): every lab-grid value is a value the
strategy already declares in its `ParamSpace` (enforced by
`test_pro_strategy_lab.py`). Shipped `Param` **defaults are never changed** — a
tuned result ships as an opt-in *preset* overlay, so the equivalence golden and
every default-assertion test stay untouched, and `rules_v1` stays byte-identical.

**Compute bound:** each cell uses its most-recent `--max-bars-per-cell` bars
(default 4000 → ~5-6 OOS folds) so the walk-forward stays tractable while
keeping strong statistical power. Runs are deterministic (fixed seeds,
cache-backed bars).

## Data inventory (Phase 0)

Real vendor history actually served (probe of 2026-07-25; full table in
`docs/backtests/strategy_lab/00_data_inventory.md`). **23 / 28 cells analyzable.**

| Symbol | 5m | 15m | 30m | 1h | 4h | 1d | 1w |
|---|---|---|---|---|---|---|---|
| BTC-USD | 30k | 30k | 30k | 21.9k | 5.6k | 939 | 135 ⚠️ |
| ETH-USD | 30k | 30k | 30k | 21.6k | 5.4k | 900 | 129 ⚠️ |
| SOL-USD | 30k | 30k | 30k | 20.1k | 5.0k | 838 | 120 ⚠️ |
| XAUUSD | 28.3k | 9.4k | 4.7k | 2.4k | 590 | 99 ⚠️ | 15 ⚠️ |

⚠️ = data-limited (< ~200 bars): all **weekly** cells and **gold daily** cannot
fit ≥2 warm-up-clearing walk-forward windows and are excluded as honest gaps.
(Crypto 5m/15m/30m were capped at the 30k request ceiling — more history is
available if a deeper study is ever needed.)

## Acceptance bar (from 12_validation_methodology.md)

A cell's result is presentable as an edge only when: the OOS (walk-forward,
embargoed) number is the headline; DSR is high and PBO is low with `n_trials`
disclosed; the parameter choice is stable across windows. Everything else is
labelled "mechanics only, not an edge measurement."

## Findings (Phase 2)

Full run 2026-07-25 (`REPORT.md`, `results.json`, gap analysis in
`01_gap_analysis.md`). 8 native strategies × 23 analyzable cells = **184 scored**
(40 data-limited); **16 cells (8.7%) cleared the guard bar.**

- **Edge is concentrated on 4h and 1d crypto.** All 16 winners are 1d (7), 4h
  (8), or 1h (1); **no** 5m/15m/30m cell and **no** gold cell passed. These
  strategies are swing/position tools, not intraday.
- **Strongest, most robust:** `trend_following_v2` ETH 1d (OOS Sharpe 0.097,
  DSR 0.998, PBO 0.15, param-stability 1.0), `volatility_breakout_v1` ETH 1d
  (0.083 / 0.937 / 0.01), `trend_following_v1` ETH 1d (0.075 / 0.980). Pyramiding
  and the volatility-squeeze breakout are the standout archetypes — consistent
  with `02_pattern_report.md`.
- **Two strategies earned no preset anywhere:** `ma_crossover_v1` (0/23 — SMA
  crosses whipsaw in ranging crypto) and `htf_momentum_v1` (0/23 — the HTF veto
  removes trades without improving the rest).
- **The ROC-momentum family is inert on fast bars:** `momentum_v1` /
  `htf_momentum_v1` / `regime_momentum_v1` place **no trades** on 5m/15m because
  `roc_threshold` is an absolute-percent move. The top improvement proposal is a
  **volatility-relative** momentum trigger (ATR-normalized ROC / z-score).
- `rules_v1` was not swept (pipeline path too slow at 4000-bar cells); left at
  its locked a-priori defaults.

**Gap-fix follow-up — `momentum_v2` (vol-relative).** The #1 improvement proposal
was built: a new strategy replacing momentum's absolute `roc_threshold` with a
vol-relative **z-score** trigger. It **closes the mechanical gap** (now trades on
every timeframe, no more no-trade cells) but is **not a strong edge** — 1 of 28
cells clears the guard (BTC 4h, OOS Sharpe 0.004, provisional). Honest takeaway:
self-scaling makes momentum tradeable intraday but adds no robust crypto alpha;
shipped as a provisional preset. Bigger levers for stronger results (not yet
done): a finer parameter search than these coarse grids, and a **portfolio**
combination of the uncorrelated 4h/1d winners.

The gap analysis (`01_gap_analysis.md`) types every failure — no-edge,
data-limited, degenerate/no-trades, strategy-level zero-edge, unstable-params —
and pairs each with a concrete improvement proposal (the backlog for making these
top-class beyond parameter tuning).

## Preset catalog (Phase 3)

The 16 guard-passing tuned sets ship in `tradingagents/pro/backtest/presets.py`
(keyed by `(strategy_id, symbol, timeframe)`), each carrying its OOS evidence
(OOS Sharpe / DSR / PBO / n_trials / param-stability). Coverage:

| Strategy | Preset cells |
|---|---|
| volatility_breakout_v1 | ETH 1d/4h/1h, SOL 1d/4h |
| trend_following_v2 | ETH 1d/4h, SOL 1d/4h |
| trend_following_v1 | ETH 1d, SOL 4h |
| mean_reversion_v1 | BTC 4h, SOL 1d |
| regime_momentum_v1 | ETH 1d/4h |
| momentum_v1 | ETH 4h |
| momentum_v2 | BTC 4h (provisional) |

Consume opt-in via `preset_params(strategy_id, symbol, timeframe)` or
`build_preset_strategy(...)`; a cell with no preset returns `None` → the caller
uses the untouched a-priori defaults. The shipped `Param` defaults are unchanged,
so the equivalence golden and every default-assertion test stay green.
Provisional passers (`most_common_share < 0.75`, e.g. `mean_reversion_v1` BTC 4h,
`volatility_breakout_v1` ETH 1h) are included but flagged in `evidence`.

## Portfolio combination (Phase 5) + vol-target (Phase 5b)

Combining the diversified daily-crypto preset winners (trend / breakout /
mean-reversion across ETH & SOL) into an equal-weight portfolio at their
walk-forward params (`02_portfolio.md`, `scripts/pro_portfolio_lab.py`):

- **Allocation is OOS-validated** (weights fit on the first 60%, measured on the
  held-out last 40%): **inverse-vol / risk-parity wins both in-sample (Sharpe
  3.16) and out-of-sample (2.41)**, beating equal-weight (2.60 / 1.93). Crucially
  **sharpe-tilt is *worse* out-of-sample than equal (1.79 < 1.93)** despite fitting
  to returns — a live demonstration that return-tilting overfits, which is why
  only the return-agnostic inverse-vol scheme is trusted.
- The chosen **inverse-vol blend: Sharpe 3.16**, Sortino 7.9, max drawdown 0.30%,
  avg pairwise correlation 0.19 — and it now beats the best single component
  (2.82) by +12%, so diversification is a genuine free lunch again.
- **Vol-target deployment:** base sizing realizes only ~1.7% annual vol (risk
  budget barely used). Sharpe is scale-invariant, so sizing to a vol target
  scales return AND drawdown by the leverage factor — e.g. **~+24%/yr at ~4.6%
  max DD (10% vol)**, **~+38%/yr at ~6.8% DD (15% vol)**. Linear-leverage upper
  bound (ignores added funding/slippage/liquidation) — validate at size.
- **Honest caveats:** one crypto regime, params fixed from walk-forward but
  equity measured over overlapping history — indicative, not guaranteed forward.

## Finer walk-forward search (Phase 6)

Genetic search over the FULL declared ranges on the top cells (`03_finer_search.md`,
`scripts/pro_finer_search.py`) vs the coarse-grid presets. Key result: the DSR
guard did its job — the two highest raw-OOS finds (`trend_following_v2` ETH 1d
0.152, SOL 1d 0.145) were **rejected** (DSR 0.47 / 0.32 — overfit). Only where a
finer config was better OOS, guard-passing, AND still walk-forward-stable
(share 1.0) did we promote it: **`volatility_breakout_v1` ETH 1d (0.083→0.121)
and SOL 1d (0.030→0.123)** — the two upgrades now in `presets.py`. Higher-OOS but
lower-stability finds (share ≤ 0.5) are documented but NOT shipped, since a
single genetic param set with low walk-forward share is a weaker basis than a
stable grid preset. Takeaway: finer search buys mostly overfitting (correctly
deflated), with a genuine, guarded upgrade on the two breakout cells.

## Reproduce

```bash
# Phase 0 — probe + cache real bars (writes data/bars_cache/, gitignored)
python scripts/pro_fetch_bars.py

# Phase 1 — harness self-test (offline, synthetic, deterministic)
python scripts/pro_strategy_lab.py --self-test

# Phase 2 — full walk-forward evaluation over the cached matrix
python scripts/pro_strategy_lab.py --max-bars-per-cell 4000
```
