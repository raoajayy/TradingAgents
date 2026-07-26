# 19 · Strategy Optimization Report (Phase 10, consolidated)

_The AI-Powered Strategy Optimization program's final deliverable. Consolidates
the strategy review, the ranked & measured improvements, the four packaged
variants, and the risk analysis into deployment recommendations. **Robustness
over historical profit** throughout: every shipped change beat a walk-forward
out-of-sample guard (Deflated Sharpe ≥ 0.6, PBO ≤ 0.5, OOS Sharpe > 0, parameter
stability ≥ 0.5); nothing curve-fit ships; experimental items are flagged; all
numbers are measured (`docs/backtests/strategy_lab/`), none invented._

## 1. Executive summary

- The platform already had strong overfitting controls (embargoed walk-forward,
  Deflated Sharpe, PBO/CSCV). This program **used** them as the ship gate rather
  than adding profit at their expense.
- **19 tuned presets** ship across 8 strategies × ETH/SOL/BTC × {1h,4h,1d} — only
  cells that cleared the guard bar out-of-sample. All are additive overlays;
  a-priori `Param` defaults are untouched (equivalence golden intact).
- The **single biggest cited win** is the **Chandelier trailing exit**: it
  lifted `volatility_breakout_v1` OOS Sharpe on two independent 4h markets
  (SOL +349%, ETH +58%) — a real effect, not a fit.
- Two enhancements were **honestly rejected** (ADX chop filter; Kelly +
  equity-curve sizing) because they did not improve OOS results — reported, not
  buried.
- Four deployment-ready **variants (A/B/C/D)** package the winners into a risk
  ladder, each reported with OOS Sharpe / MAR / drawdown / Monte-Carlo and
  **disclosed leverage**.
- **Recommendation: Variant B (Balanced)** for a live paper-trading pilot —
  OOS Sharpe 2.41, max DD 0.9%, prob_loss 0%, 3x leverage cap.

## 2. Strategy review (what exists, how it does)

Eight native order-book strategies + the rules pipeline, each a recognized
archetype (trend/Donchian, pyramiding trend, MA-cross, breakout/squeeze,
momentum, vol-relative momentum, HTF-confirmed momentum, regime-gated momentum,
mean-reversion). Per-preset benchmark vs buy-&-hold (`05_benchmark.md`):

- **Every shipped preset has positive alpha and near-zero beta.** They stayed
  green while buy-&-hold fell 34–55% over the same ETH/SOL windows — a low-beta,
  drawdown-aware return stream, in-market only a fraction of the time.
- Standouts by Calmar (return / max-DD): `volatility_breakout_v1` ETH 1d
  (Calmar 15.2), SOL 1d (9.1); `trend_following_v2` ETH 1d (3.7).
- Weakest / provisional: `volatility_breakout_v1` ETH **1h** (prob_loss 37%,
  Calmar 0.17) — flagged provisional; `ma_crossover_v1` ETH 1d (prob_loss 26%).

## 3. Ranked improvements (measured deltas)

| Rank | Change | Evidence | Measured effect | Verdict |
|---|---|---|---|---|
| 1 | **Chandelier exit** (Le Beau) on breakouts | SO-C1 / `enh/` | `volatility_breakout_v1` SOL 4h OOS **0.0157→0.0705** (+349%, DSR 0.99); ETH 4h **0.0338→0.0535** (DSR 0.99) | ✅ shipped (2 cells upgraded) |
| 2 | **HTF size-scaler** `htf_momentum_v2` | SO-C3 / `enh_htf/` | ETH 4h OOS **0.0311**, DSR 0.847 (v1 veto earned 0) | ✅ shipped |
| 3 | **Inverse-vol portfolio** (risk parity) | SL-5/8 / `02_portfolio.md` | OOS Sharpe **2.41**, beats equal-weight & (overfit) sharpe-tilt OOS | ✅ shipped |
| 4 | `ma_crossover_v1` first robust cell | SO-C-eval | ETH 1d OOS **0.0642**, DSR 0.737 (base params) | ✅ shipped (provisional) |
| 5 | Finer genetic search on top cells | SL-7 / `03_finer_search.md` | ETH/SOL 1d breakout OOS ~1.5–4× higher, still stable | ✅ shipped (2 cells) |
| — | ADX chop filter (Wilder) | SO-C2 | winner always `adx_filter=off`; no OOS gain | ❌ rejected (honest) |
| — | ATR (non-Chandelier) trail | SO-C1 | no atr-mode cell cleared the bar | ❌ rejected |
| — | Kelly + equity-curve sizing | SO-C4 | ETH 1d Sharpe **1.558→0.947** (both on, −39%) | ❌ EXPERIMENTAL, not shipped |

## 4. Before / after

| Dimension | Before this program | After |
|---|---|---|
| Guard-passing presets | 16 (SL) | **19** (+Chandelier upgrades ×2, +htf_momentum_v2, +ma_crossover) |
| Breakout exits | % trail only | + ATR + **Chandelier** (Chandelier wins on 4h) |
| HTF momentum | binary veto (0 presets) | continuous **size-scaler** (1 preset) |
| Robustness reporting in the Lab | walk-forward + DSR/PBO | + **Monte-Carlo** (04), **benchmark/alpha-beta** (05), **regime breakdown** (06), **sensitivity heatmaps** (07) |
| Packaged products | ad-hoc portfolio | **4 named variants** A/B/C/D with disclosed leverage (08) |
| Advanced sizing | none | Kelly + equity-curve available (EXPERIMENTAL, off, measured-negative) |

## 5. The four variants (SO-D, `08_variants.md`)

| Variant | Profile | OOS Sharpe | CAGR | Max DD | MAR | Leverage | prob_loss |
|---|---|---:|---:|---:|---:|---:|---:|
| A · Conservative | daily high-DSR, 8% vol | 1.95 | +5.5% | 0.6% | 9.6 | 2.0x | 0% |
| **B · Balanced (rec.)** | 6-cmpt daily, 15% vol | **2.41** | +8.3% | 0.9% | 9.3 | 3.0x | 0% |
| C · Aggressive | pyramid + 4h breakout, 22% vol | 2.62 | +32.8% | 5.9% | 5.5 | 4.0x | 0% |
| D · Regime-aware | regime_momentum core, 15% vol | 2.31 | +14.2% | 4.8% | 2.9 | 3.0x | 0% |

Clean monotonic risk ladder A→C (return and drawdown both rise). **Leverage
caveat:** all four hit their leverage cap (unlevered vol below target), so CAGR
is leverage-dependent — not free alpha. C is the most leverage-exposed.

## 6. Risk analysis

- **Monte-Carlo (returns/trade bootstrap, 04 & 08):** the shipped daily
  presets and variants A/B show **prob_loss 0%** with p5 final equity above the
  stake; the provisional cells (`volatility_breakout_v1` ETH 1h prob_loss 37%,
  `ma_crossover_v1` ETH 1d 26%) are the honest fragility flags.
- **Regime (06):** trend/breakout presets earn the bulk of P&L in trending
  regimes and give some back in ranging — behaving as designed, not overfit to a
  single regime cluster.
- **Parameter sensitivity (07):** on the OOS-holdout metric, all 8 top cells sit
  on positive **plateaus** (neighbouring parameters keep the edge) — the visual
  corroboration of the DSR/PBO verdict. (An earlier in-sample metric looked
  spiky; the report documents why the holdout metric is the right one.)
- **Structural (no look-ahead/leakage):** enforced by `snapshot_at(i) ≤ i`,
  next-bar fills, embargoed walk-forward, and equivalence/structural tests. The
  one harness bug found (htf_momentum_v2 HTF not fed in the sweep) was caught,
  fixed, and the affected preset re-validated with the feature active.

## 7. Deployment recommendations

1. **Pilot Variant B (Balanced)** in paper trading first — best OOS
   Sharpe/drawdown trade-off, 0% Monte-Carlo prob_loss, moderate (3x-capped)
   leverage. Offer A to risk-averse users, C only with the leverage caveat shown
   in the UI, D as the regime-gated option.
2. **Surface the leverage multiplier and prob_loss** on any variant the UI
   exposes — never show CAGR without them.
3. **Treat the 1h and provisional cells as watch-list, not deploy** until they
   accumulate more OOS history (their Monte-Carlo prob_loss is high).
4. **Keep the guard bar as the ship gate** for any future preset; keep Kelly /
   equity-curve overlays **off** unless a specific market shows an OOS gain.
5. **Re-run the Lab on a schedule** as new bars accumulate; presets are
   data-dated (2026-07) and should be re-validated, not trusted indefinitely.

## 8. Honest limitations

- History is **cached crypto** (ETH/SOL/BTC) over a finite window skewed bearish
  for buy-&-hold; results may not transfer to other assets/regimes.
- Backtest fills/costs are modelled, not live; slippage in thin markets is a real
  risk the sim under-states.
- Variant CAGR is **leverage-scaled**; de-levered returns are the honest single-
  strategy figures in `results.json`.
- Nothing here is investment advice; it is a research/engineering artifact.

## 9. Deliverables index

- Presets & guard: `presets.py`, `01_gap_analysis.md`, `REPORT.md`, `results.json`
- Robustness: `04_montecarlo.md`, `05_benchmark.md`, `06_regime_breakdown.md`
- Sensitivity: `07_sensitivity.md` + `sensitivity/*.png`
- Variants: `variants.py`, `08_variants.md`
- Portfolio/allocation: `02_portfolio.md`, `03_finer_search.md`
- Evidence: `18_literature_review.md` (this report's citations & measured impact)
