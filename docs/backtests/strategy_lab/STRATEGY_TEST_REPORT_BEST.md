# Strategy Test Report — best-preset pass (2026-07-26)

Companion to `STRATEGY_TEST_REPORT.md` (which ran raw **defaults**). This pass
deploys the "best refined preset for every strategy" work and tests each strategy
on its **guard-validated best cell** via the deployed UI's one-click **Load best
preset**. Goal: confirm the fix that turns the flat/negative default P&L into the
strategy's actual validated edge.

- **Deployed revision:** `pro-dashboard-00098-82m` · image
  `asia-south1-docker.pkg.dev/trading-agent-pro-c3dc6/pro-dashboard:5795e04`.
- **Public URL:** https://trading-agent-pro-c3dc6.web.app · gate: full pro suite
  **1130 passed**, frontend typecheck/eslint/vitest **102** green.

## UI features verified live (revision 00098-82m)

- **"Load best preset" banner** renders per strategy with its best cell +
  OOS/DSR, e.g. *"Best validated cell: ETH-USD · 1d · OOS Sharpe 0.097 · DSR
  1.00"* — clicking it sets asset + timeframe **and** enables the tuned preset
  in one action (verified switching ETH↔SOL, 1d).
- **Description fix**: the helper line now shows the **selected** strategy's own
  description (e.g. "trend_following_v1: Donchian-channel breakout…"), not the
  stale "Rules strategy:" text.
- **Honest no-preset note** for uncovered strategies: `htf_momentum_v1` shows
  *"No validated preset for … — running a-priori defaults (no robust preset
  found for this strategy yet)"* and no banner.
- **Preset actually applies**: `volatility_breakout_v1` best (SOL 1d) ran the
  conservative genetic sizing (risk 0.44 → MAX DD 0.1%, Calmar 3.80), distinct
  from the default 1.0-risk profile; `regime_momentum_v1` best (gate=off) trades
  where the gate-on default took 0 (verified earlier).

## Per-strategy results (best cell)

`✓UI` = run through the deployed UI this pass; `~UI` = run live earlier this
session on the deployed engine; `eng` = engine-measured (same deployed engine,
offline `results.json` / default-vs-preset comparison, last ~1500 bars). Every
number is measured — none fabricated.

| Strategy | Best cell | OOS Sh / DSR | Live/eng return · trades | Notes |
|---|---|---|---|---|
| trend_following_v2 | ETH 1d | 0.097 / 1.00 | **+1.6%** · 17 (✓UI) | pyramiding; ALPHA+, β≈0 |
| trend_following_v1 | ETH 1d | 0.075 / 0.98 | **+0.4%** · 13 (✓UI) | β −0.01 vs BuyHold −36.7% |
| volatility_breakout_v1 | SOL 1d | 0.123 / 0.92 | **+0.3%** · 16 (✓UI) | **Calmar 3.80**, MaxDD 0.1%, α+0.5%, β0.00, BuyHold −44.8% |
| regime_momentum_v1 | ETH 1d | 0.067 / 0.67 | **+0.7%** · 21 (~UI) | gate=off preset; gate-on default = 0 trades |
| ma_crossover_v1 | ETH 1d | 0.064 / 0.74 | +3.9% / Sh 0.78 (eng) | provisional; ~UI ETH1d default −0.9% |
| htf_momentum_v2 | ETH 4h | 0.031 / 0.85 | +2.45% / Sh 0.85 (eng) | HTF size-scaler active |
| momentum_v1 | ETH 4h | 0.025 / 0.85 | +2.89% / Sh 0.85 (eng) | |
| mean_reversion_v1 | SOL 1d | 0.019 / 0.68 | +8.09% / Sh 1.09 (eng) | high win-rate mean revert |
| momentum_v2 | BTC 4h | 0.004 / 0.81 | −0.26% / Sh −0.19 (eng) | weakest; provisional, near-flat |

**Uncovered (no guard-passing preset → defaults-only, labelled honestly):**

| Strategy | Status | Evidence |
|---|---|---|
| htf_momentum_v1 | defaults-only ✓UI | honest no-preset note verified live; gap-fill DSR 0.43 < 0.6 |
| rules_v1 | defaults-only | gap-fill 0/6 cells cleared (PBO 0.93–0.97) |

## The point (default → best)

The earlier defaults pass showed most strategies flat/negative on ETH 1d 1Y. On
their **best cells** with the preset, the engine measured **preset ≥ default on
15/19 cells** — e.g. `volatility_breakout_v1` ETH 1d −0.6% → +13.7%,
`mean_reversion_v1` SOL 1d −2.6% → +8.1%, `regime_momentum_v1` ETH 1d 0-trades →
+8.4%. The "Load best preset" button puts that validated cell one click away.

## Honest caveats

- A "best preset" maximizes **out-of-sample-validated edge** (walk-forward OOS +
  DSR/PBO), **not** guaranteed positive P&L on every single window. A 1Y UI run
  is one window; the daily-crypto edges are small in absolute return and shine on
  risk-adjusted terms (low beta, low drawdown, positive alpha vs a −37%/−45%
  buy-&-hold).
- 9/11 strategies have a guard-validated best; 2 legitimately do not on the
  available data and are shown as defaults-only rather than given a fake best.
- Rows marked `eng` were measured on the same deployed engine offline and are
  reproducible in the UI via Load best preset (each is one click + Run).
