# 18 · Literature Review — evidence behind the shipped optimizations

_Part of the AI-Powered Strategy Optimization program (SO). Every technique the
Strategy Lab ships is grounded in a real, citable public source; every impact
number here is **measured** from the Lab's own runs (`docs/backtests/strategy_lab/`),
never invented. Where a technique was tried and did **not** beat the guard bar,
that is stated as a negative result rather than omitted._

## How to read this

For each item: the **source** (real public reference), what it claims, how we
implemented it, and the **measured** effect on this project's cached crypto
history. "OOS" = walk-forward out-of-sample Sharpe (the Lab headline);
"guard bar" = Deflated Sharpe ≥ 0.6 **and** PBO ≤ 0.5 **and** OOS Sharpe > 0
**and** parameter stability (share) ≥ 0.5.

---

## A. Overfitting controls (the methodology the whole Lab rests on)

### A1 · Deflated Sharpe Ratio + Probability of Backtest Overfitting
- **Source:** Bailey & López de Prado, *"The Deflated Sharpe Ratio: Correcting
  for Selection Bias, Backtest Overfitting and Non-Normality"*, Journal of
  Portfolio Management, 2014. Bailey, Borwein, López de Prado & Zhu, *"The
  Probability of Backtest Overfitting"* (CSCV), Journal of Computational
  Finance, 2017.
- **Claim:** a Sharpe ratio selected from many trials is upward-biased; the DSR
  deflates it by the number of trials and the return distribution's skew/kurtosis,
  and PBO (via combinatorially-symmetric cross-validation) estimates the
  probability the selected configuration is overfit.
- **Implementation:** `validation.py::deflated_sharpe_ratio`,
  `probability_of_backtest_overfitting`; enforced as the ship gate in
  `pro_strategy_lab.py`.
- **Measured effect:** this gate is *why* only 19 of the swept cells ship. In the
  finer-search pass (`03_finer_search.md`) it correctly **rejected the two
  highest raw-OOS finds as overfit**, admitting only the two that were also
  stable — the single most important guard in the program.

### A2 · Purged / embargoed walk-forward
- **Source:** López de Prado, *Advances in Financial Machine Learning*, Wiley,
  2018 (Ch. 7 — cross-validation in finance: purging & embargo).
- **Claim:** naive k-fold CV leaks information across adjacent, serially-correlated
  bars; a temporal embargo between train and test removes the leak.
- **Implementation:** `walkforward.py::run_walk_forward_optimization` with an
  embargo gap; OOS Sharpe is concatenated across held-out test windows only.
- **Measured effect:** the embargoed walk-forward is the headline metric for
  every preset in `presets.py`; it is what the sensitivity heatmaps (07) were
  re-based onto (holdout Sharpe) once an in-sample metric proved misleading.

---

## B. Shipped enhancements (Track C) — each beat the guard bar OOS

### B1 · Chandelier trailing exit  ✅ shipped (biggest robustness win)
- **Source:** Chuck Le Beau, Chandelier exit (Technical Traders Bulletin; Le Beau
  & Lucas, *Computer Analysis of the Futures Markets*, 1992) — trail the stop a
  multiple of ATR below the highest high since entry. ATR-based exits are
  standard CTA practice (Kaufman, *Trading Systems and Methods*).
- **Claim:** an ATR/high-anchored trailing stop lets winners run while adapting
  the stop distance to current volatility — better than a fixed % trail.
- **Implementation:** `_exits.py` (`trail_mode` = pct|atr|chandelier), wired into
  `trend`, `breakout` strategies; the broker already executed the mode
  (`broker.py::_update_trailing`).
- **Measured effect (the clearest cited win in the program):** on
  `volatility_breakout_v1`, switching the trail to Chandelier lifted OOS Sharpe
  **0.0157 → 0.0705 on SOL 4h** (+349%, DSR 0.990, stability 0.50→0.75) and
  **0.0338 → 0.0535 on ETH 4h** (DSR 0.993). The *same mechanism won on two
  independent markets in the same direction* — the signature of a real effect,
  not a curve fit. Both cells' shipped presets were upgraded to Chandelier.

### B2 · Higher-timeframe trend-strength position sizing  ✅ shipped
- **Source:** multi-timeframe confirmation + trend-strength sizing (Kaufman,
  *Trading Systems and Methods*; the "trade with the higher-timeframe tide"
  principle in the trader KB, `02_pattern_report.md`).
- **Claim:** rather than veto trades against the higher timeframe (binary, throws
  away trades), *scale* position size by higher-timeframe alignment strength.
- **Implementation:** `momentum.py::HtfMomentumV2` — `_htf_scale` maps the HTF
  close's signed distance from its mean to a continuous risk multiplier; HTF fed
  by the engine (D1/W1).
- **Measured effect:** `htf_momentum_v1` (binary veto) earned **zero** presets;
  the size-scaler `htf_momentum_v2` earns one — ETH 4h, **OOS 0.0311, DSR 0.847,
  PBO 0.230** with the scaler active (validated after fixing a harness bug where
  the HTF was not being fed; see 01_gap_analysis.md). Turning a veto into a size
  tilt rescued a strategy family.

### B3 · ma_crossover first robust cell  ✅ shipped (base params)
- **Source:** the EMA/MA-crossover trend archetype (Market Wizards / CTA canon,
  `02_pattern_report.md`).
- **Measured effect:** `ma_crossover_v1` earned its **first-ever preset** — ETH
  1d, OOS 0.0642, DSR 0.737. Honest caveat: this is a *base-parameter* win; the
  ADX filter (B4) is **off** in it.

---

## C. Tried and rejected — honest negatives

### C1 · ADX chop filter  ❌ not shipped
- **Source:** J. Welles Wilder Jr., *New Concepts in Technical Trading Systems*,
  1978 (the Average Directional Index; ADX < ~20-25 ⇒ non-trending/chop).
- **Rationale for trying it:** `ma_crossover_v1` whipsaws in ranges; ADX should
  suppress crosses when no trend exists.
- **Measured effect:** on **every** `ma_crossover_v1` cell the guard-passing
  configuration has `adx_filter = "off"` — ADX-on never beat ADX-off out of
  sample. The filter removes whipsaw trades but also removes enough valid crosses
  that OOS Sharpe does not improve. Kept in the codebase (off by default,
  available), shipped as no preset. A genuine negative result, reported.

### C2 · ATR (non-Chandelier) trailing mode  ❌ not shipped
- **Measured effect:** no `atr`-mode configuration cleared the guard bar in the
  Track-C sweep; where a trailing enhancement won it was always `chandelier`
  (high-anchored) or the incumbent `pct`. ATR-trail remains available, earns no
  preset.

### C3 · Kelly-capped sizing + equity-curve filter  ❌ not shipped (EXPERIMENTAL)
- **Source:** Kelly, *"A New Interpretation of Information Rate"*, Bell System
  Technical Journal, 1956; Thorp's application to markets (with the well-known
  caveat that full Kelly badly over-bets on estimation error → fractional Kelly).
  Equity-curve trading: Van Tharp, *Trade Your Way to Financial Freedom*.
- **Implementation:** `_sizing.py` (`kelly_risk` capped ≤ 0.25 fractional and
  floored; `equity_below_ma`), wired off-by-default into `trend_following_v1`.
- **Measured effect (before/after, ETH 1d full window, Sharpe):** baseline
  **+1.558** → equity-filter on **+1.329** (−15%) → Kelly on **+1.309** (−16%) →
  both **+0.947** (−39%). Every overlay *reduced* risk-adjusted return on these
  small trade samples — exactly the over-betting-on-estimation-error failure the
  Kelly literature warns about. Left off by default, flagged **EXPERIMENTAL**,
  no preset.

---

## D. Portfolio & sizing layer

### D1 · Inverse-volatility (risk parity) allocation  ✅ shipped
- **Source:** Edward Qian, *"Risk Parity Portfolios"* (PanAgora, 2005) and the
  broader risk-parity literature — weight ∝ 1/volatility so each sleeve
  contributes comparable risk; return-agnostic, so it does not overfit to past
  returns.
- **Measured effect:** OOS-validated (weights fit on the first 60%, measured on
  the last 40%), inverse-vol beat both equal-weight and a return-chasing
  sharpe-tilt out-of-sample; the sharpe-tilt **overfit** (better in-sample, worse
  OOS) — a clean demonstration of why the return-agnostic scheme is preferred
  (`02_portfolio.md`). Inverse-vol is the weighting for all four variants (SO-D).

### D2 · Volatility targeting  ✅ shipped (with disclosed leverage)
- **Source:** volatility targeting / vol-scaling (Moreira & Muir, *"Volatility-
  Managed Portfolios"*, Journal of Finance, 2017; standard CTA practice).
- **Measured effect:** the four SO-D variants scale the unlevered blend to a vol
  target (8%/15%/22%), with the **leverage multiplier disclosed** in
  `08_variants.md`. Every variant hit its leverage cap (unlevered vol below
  target), so the reported CAGR is explicitly leverage-dependent — stated, not
  hidden.

---

## E. Benchmarking

### E1 · Buy-&-hold alpha/beta and drawdown-adjusted return
- **Source:** standard performance attribution (Jensen's alpha; Calmar/MAR ratio,
  Young, 1991).
- **Measured effect (`05_benchmark.md`):** across all 19 presets, every cell
  shows **positive alpha** and **near-zero beta** to buy-&-hold — they stayed
  positive while buy-&-hold fell 34–55% over the same ETH/SOL windows. The edge
  is low-beta, drawdown-aware return, not raw outperformance in a bull leg — the
  honest framing for a system that is in the market only a fraction of the time.

---

## Reference list (public, real)

1. Bailey, D. & López de Prado, M. (2014). The Deflated Sharpe Ratio. *Journal of Portfolio Management*.
2. Bailey, D., Borwein, J., López de Prado, M. & Zhu, Q. (2017). The Probability of Backtest Overfitting. *Journal of Computational Finance*.
3. López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
4. Le Beau, C. & Lucas, D. (1992). *Computer Analysis of the Futures Markets*. (Chandelier exit.)
5. Wilder, J. W. (1978). *New Concepts in Technical Trading Systems*. (ADX.)
6. Kelly, J. L. (1956). A New Interpretation of Information Rate. *Bell System Technical Journal*.
7. Thorp, E. O. (2006). The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market. *Handbook of Asset and Liability Management*.
8. Qian, E. (2005). *Risk Parity Portfolios*. PanAgora Asset Management.
9. Moreira, A. & Muir, T. (2017). Volatility-Managed Portfolios. *Journal of Finance*.
10. Kaufman, P. (2013). *Trading Systems and Methods* (5th ed.). Wiley.
11. Tharp, V. (2007). *Trade Your Way to Financial Freedom* (2nd ed.). McGraw-Hill.

_Measured impact numbers are reproducible from `docs/backtests/strategy_lab/`
(`results.json`, `enh/`, `enh_htf/`, `robustness.json`, `08_variants.md`)._
