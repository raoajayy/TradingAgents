# Strategy Lab — Gap Analysis (Phase 2)

_Generated from `results.json` (2026-07-25). Objective `sharpe`, walk-forward OOS
+ DSR/PBO guard bar. 8 native strategies × 23 analyzable cells scored (184
scored, 40 data-limited). **16 cells (8.7%) earned a preset.** `rules_v1` was
not swept (pipeline path too slow at 4000-bar cells; see note at end)._

## Headline

- **Edge lives on 4h and 1d crypto, nowhere else.** All 16 guard-passing cells
  are 1d (7), 4h (8), or 1h (1). **Zero** cells passed on 5m/15m/30m for any
  strategy, and **zero** on gold.
- **Two strategies earned no preset anywhere:** `ma_crossover_v1` (0/23) and
  `htf_momentum_v1` (0/23). These are the biggest strategy-level gaps.
- **The ROC-momentum family is silent on fast bars:** `momentum_v1`,
  `htf_momentum_v1`, `regime_momentum_v1` place **no trades at all** on 5m (9
  cells) and 15m (4 cells) — the threshold never triggers.

| strategy | pass | no-edge | no-trades | data-limited |
|---|---|---|---|---|
| volatility_breakout_v1 | 5 | 18 | 0 | 5 |
| trend_following_v2 | 4 | 19 | 0 | 5 |
| mean_reversion_v1 | 2 | 21 | 0 | 5 |
| regime_momentum_v1 | 2 | 17 | 4 | 5 |
| trend_following_v1 | 2 | 21 | 0 | 5 |
| momentum_v1 | 1 | 18 | 4 | 5 |
| htf_momentum_v1 | 0 | 15 | 5 | 8 |
| ma_crossover_v1 | 0 | 23 | 0 | 5 |

## Typed gaps + improvement proposals

**(a) No out-of-sample edge — the majority (152 cells).** Most (strategy, market,
timeframe) combinations fail DSR/PBO. Concentrated on 5m/15m/30m: these are
swing/position strategies and there is no robust intraday edge in their current
form. → *Proposal:* scope/position these strategies as **≥1h** tools; sub-hourly
trading needs microstructure-aware logic (a separate strategy class), not
re-tuning these.

**(b) Data-limited — 40 cells (5 per strategy).** All **weekly** cells (BTC/ETH/SOL
~120–135 bars) and **gold daily/weekly** (99/15 bars) cannot fit ≥2 warm-up-
clearing walk-forward windows, so they are recorded unscored (never faked).
→ *Proposal:* author a committed deeper-history bar cache (or a `HistoricalCorpus`)
if weekly/position-trading edge needs a verdict; today it's honestly "unmeasured."

**(c) Degenerate / no-trades — 13 cells (ROC family on 5m/15m).** `roc_threshold`
is an **absolute percent** move over `roc_period` bars; on a 5m bar a 3–8% move
in 14 bars is vanishingly rare, so the strategy never enters. → *Proposal (highest
value):* make the momentum trigger **volatility-relative** — ATR-normalized ROC
or a rolling z-score — so it self-scales across timeframes instead of going inert.
This one change would likely make the momentum family tradeable intraday.

**(d) Strategy-level zero-edge.**
- `ma_crossover_v1` (0/23): SMA golden/death crosses **whipsaw** in ranging
  crypto — every window is chop-dominated. → *Proposal:* gate crossovers with a
  trend-strength filter (ADX or SMA-slope) so they only fire in a directional
  regime, or retire it as a standalone in favor of `trend_following_*`.
- `htf_momentum_v1` (0/23): the HTF close-vs-SMA **veto** mostly just removes
  trades from `momentum_v1` without improving what remains, and it shares
  momentum's absolute-threshold problem. → *Proposal:* turn HTF alignment into a
  **position-size scaler** (bigger when aligned) rather than a hard veto, and use
  HTF **slope** not just close-vs-mean.

**(e) Unstable parameters (lower-confidence passes).** Several passers have
`most_common_share = 0.50` (walk-forward chose different params across windows) —
e.g. `mean_reversion_v1` BTC 4h (OOS Sharpe 0.002, essentially flat) and
`volatility_breakout_v1` ETH 1h (PBO 0.42). They clear the bar but sit near it.
→ *Proposal:* treat `share < 0.75` presets as provisional; prefer the high-DSR,
high-share winners (e.g. `trend_following_v2` ETH 1d: DSR 0.998, share 1.00).

## Strongest, most robust edges (ship with confidence)

| Strategy | Market/TF | OOS Sharpe | DSR | PBO | Param stability |
|---|---|---|---|---|---|
| trend_following_v2 | ETH 1d | 0.097 | 0.998 | 0.15 | 1.00 |
| volatility_breakout_v1 | ETH 1d | 0.083 | 0.937 | 0.01 | 1.00 |
| trend_following_v1 | ETH 1d | 0.075 | 0.980 | 0.21 | 1.00 |
| regime_momentum_v1 | ETH 4h | 0.040 | 0.918 | 0.04 | 0.75 |
| trend_following_v2 | ETH 4h | 0.037 | 0.904 | 0.07 | 0.75 |

Pyramiding (`trend_following_v2`) and the volatility-squeeze breakout are the
standout archetypes on daily crypto — consistent with the trader research
(`02_pattern_report.md`).

## Follow-up: gap (c) fix validated — `momentum_v2` (vol-relative)

The proposed fix for the ROC-momentum no-trades gap was built and re-evaluated:
`momentum_v2` replaces the absolute `roc_threshold` with a **z-score** trigger
(cumulative move ÷ the window's own return σ, √period-scaled), so entries scale
to each timeframe's realized volatility.

- **Mechanical gap closed:** `momentum_v2` now places trades on **every**
  timeframe including 5m/15m — the degenerate `oos=0.000 / pbo=1.00` no-trade
  cells are gone.
- **But it is not a strong edge on crypto:** of 28 cells only **1** clears the
  guard bar (BTC 4h: DSR 0.81, PBO 0.31, OOS Sharpe **0.004** — near-flat,
  `share` 0.5, provisional). Most cells are net-negative; the few positive-raw
  ones (ETH 1d 0.076, XAU 4h 0.039) **fail** the guard (overfit) and are
  correctly rejected.
- **Takeaway:** self-scaling makes momentum *tradeable* across timeframes, but
  fast-timeframe momentum has no robust edge on this crypto history. The fix is
  worth keeping (removes a dead capability) and is shipped as a provisional
  preset; it is not a source of top-class returns on its own. The larger levers
  for stronger results are a **finer parameter search** than these coarse grids
  and a **portfolio combination** of the uncorrelated 4h/1d winners.

## Note on `rules_v1`

`rules_v1` runs the full deterministic rules pipeline per bar; at 4000-bar cells
its walk-forward is orders of magnitude slower than the native strategies and did
not complete a single cell in the sweep window. It is **excluded from this run**
and left at its a-priori defaults (locked by the equivalence golden). Its three
tunables (`tp_ladder`, `min_risk_reward`, `stop_cooldown_bars`) flow through
`config.risk` and were already validated in the R-ladder work (SQ/LG). A dedicated
smaller-window `rules_v1` sweep can be run later if a preset is wanted.

## SO-C — evidence-based enhancement re-run (2026-07-26)

Four additive, off-by-default enhancements (Track C) were swept over the
productive 4h/1d crypto cells (BTC/ETH/SOL) via the same walk-forward + DSR/PBO
guard. Results in `docs/backtests/strategy_lab/enh/results.json`. Only configs
clearing the bar (OOS Sharpe > 0, DSR ≥ 0.6, PBO ≤ 0.5, `share` ≥ 0.5) shipped.

### Shipped (guard-passing wins)

| Enhancement | Cell | Config | OOS | DSR | PBO | share | vs prior |
|---|---|---|---|---|---|---|---|
| **C1 Chandelier exit** | volatility_breakout_v1 SOL 4h | `trail_mode=chandelier` | 0.0705 | 0.990 | 0.19 | 0.75 | pct 0.0157 → **+349%**, share 0.50→0.75 |
| **C1 Chandelier exit** | volatility_breakout_v1 ETH 4h | `trail_mode=chandelier` | 0.0535 | 0.993 | 0.01 | 0.75 | pct 0.0338 → **+58%** |
| **C3 HTF size-scaler** | htf_momentum_v2 ETH 4h | `roc_period=10, thr=4.0` (HTF 1d/1w active) | 0.0311 | 0.847 | 0.23 | 0.50 | v1 earned **0** presets → v2 earns one |
| (base) ma_crossover_v1 | ETH 1d | `fast=8, slow=30` | 0.0642 | 0.737 | 0.41 | 0.50 | first-ever preset for this strategy |

### Tried and rejected (honest negatives)

- **C2 — ADX chop filter (rejected).** On every ma_crossover_v1 cell the
  guard-passing winner has `adx_filter="off"`; ADX-on never beat ADX-off out of
  sample. The filter removes whipsaw trades but also removes enough valid
  crosses that OOS Sharpe does not improve. Kept in the codebase (off by
  default, available) but **not** shipped as any preset. ma_crossover_v1's first
  preset (ETH 1d) is a base-parameter win, *not* an ADX-filter win.
- **C1 — ATR trailing mode (rejected in favour of Chandelier).** No `atr`-mode
  config cleared the bar; where a trailing enhancement won it was always
  `chandelier` (or the existing `pct`). ATR-trail stays available but earns no
  preset.
- **C4 — Kelly-capped sizing + equity-curve filter (EXPERIMENTAL, not shipped).**
  Excluded from the combinatorial grid (they are risk overlays, not signal
  params). Measured full-window before/after on trend_following_v1 ETH 1d (900
  bars, sharpe): baseline **+1.558** → equity_filter=on **+1.329** (−15%) →
  kelly_sizing=on **+1.309** (−16%) → both on **+0.947** (−39%). Every overlay
  *reduces* risk-adjusted return here — the equity-curve filter sits out valid
  re-entries and Kelly only ever scales *down* (it is capped from scaling up).
  Left off-by-default and **flagged EXPERIMENTAL**; no preset. (Cited rationale —
  over-betting on estimation error, Kelly 1956 / Thorp — is exactly why full
  Kelly is avoided; on these small samples even fractional Kelly hurts.)

### Net effect

Catalog grew 17 → **19** presets. The genuine, cited robustness win is the
**Chandelier exit on 4h breakouts** (Le Beau): it materially lifted OOS Sharpe
*and* cross-window stability on two independent cells (ETH, SOL) — not a
curve-fit, since the same mechanism won on two markets with the same direction
of effect. The HTF size-scaler (C3) rescued a strategy family that previously
earned nothing. C2 and C4 are documented negatives — reported, not buried.

## BP — "best preset for every strategy": gap-fill for the 2 uncovered strategies (2026-07-26)

Goal: give `htf_momentum_v1` and `rules_v1` (the only strategies with **no** preset) a fair chance to earn one, so every strategy can surface a best refined option. Walk-forward + DSR/PBO over BTC/ETH/SOL, same guard bar (OOS>0, DSR≥0.6, PBO≤0.5, share≥0.5).

**Harness correctness fix (found here):** `pro_strategy_lab.py::evaluate_cell` passed a strategy's declared `htf_timeframes` unfiltered, so an HTF-consuming strategy on a **1d/1w base** raised *"htf 1d must be coarser than the bar timeframe"* and those cells errored out (this is also why `htf_momentum_v1`'s 1d cells were never scored in earlier runs). Now filtered to strictly-coarser frames, mirroring the dashboard job (`backtest_job.py`).

**Results — both remain defaults-only (0 guard-passers):**
- `rules_v1` (BTC/ETH/SOL × 4h/1d, capped 1200 bars): best raw-OOS cells (SOL 1d +0.045, SOL 4h +0.030) fail on PBO≈0.93–0.97 (overfit); the DSR-respectable BTC 4h is negative OOS. **No robust preset.**
- `htf_momentum_v1` (4h + 1d, HTF active after the fix): best is ETH 1d OOS +0.049 but **DSR 0.43 < 0.6**; all others fail DSR or PBO. **No robust preset.**

**Decision:** ship nothing for these two — a guard-failing fit is exactly what the robustness bar exists to reject. Both stay at a-priori defaults and are labelled honestly in the UI ("no robust preset found for this strategy yet"). Coverage: **9/11** strategies have a guard-validated best cell; 2 legitimately do not on the available data.

## MF — momentum_v2 entry_sigma recalibration (2026-07-26)

**Defect:** `momentum_v2`'s a-priori `entry_sigma` domain was 1.0–4.0 (default 2.0) and the Lab grid searched {1.5, 2.0, 3.0}. A z-score entry at ≥1.5σ fires only on **climax** moves (a ~2σ cumulative move over 14 bars ≈ an 11% push on ETH 4h) — which mean-revert, not continue. So the strategy was effectively *fading tops/bottoms in the momentum direction* and had no robust edge (its only prior preset, BTC 4h σ=2.0, was near-flat: OOS 0.004). Crucially the region where the momentum edge actually lives — a **moderate** vol-relative move (~0.5σ), the same moderate-trend edge `momentum_v1`'s absolute ROC captures (ETH 4h +19.7% live) — was **outside the searched grid and below the a-priori floor**, so it could never be found.

**Fix:** widened the `entry_sigma` domain to 0.5–4.0 (default 1.0) and retargeted the Lab grid to {0.5, 1.0, 1.5}. Re-ran walk-forward + DSR/PBO over BTC/ETH/SOL × 4h/1d.

**Result — two real guard-passers, both at σ=0.5:**

| cell | entry_sigma / roc | OOS | DSR | PBO | vs old |
|---|---|---|---|---|---|
| ETH 1d (new best) | 0.5 / 10 | 0.0528 | 0.610 | 0.30 | **12×** the retired BTC-4h 0.004 |
| ETH 4h | 0.5 / 10 | 0.0304 | 0.787 | 0.45 | new |

The σ=1.5 climax cells (SOL, BTC) still fail (DSR<0.6 or PBO>0.5) — confirming the edge is specifically the moderate move, not the extreme. The weak BTC-4h σ=2.0 preset is retired; momentum_v2's recommended best is now **ETH 1d**. Default `entry_sigma` lowered 2.0 → 1.0 so un-preset runs also target continuation, not climaxes.
