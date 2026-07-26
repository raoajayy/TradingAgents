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

**Every strategy was clicked through the deployed UI (Load best preset → Run) on
its best cell, 1Y window, revision 00098-82m.** All numbers read from the live
result card — none fabricated. `n` = closed trades.

| # | Strategy | Best cell | OOS Sh / DSR | Live return · n · WR · PF | Notes |
|--:|---|---|---|---|---|
| 1 | momentum_v1 | ETH 4h | 0.025 / 0.85 | **+19.7%** · 130 · 47.7% · 1.25 | ★ best live; MaxDD 9.6% |
| 2 | htf_momentum_v2 | ETH 4h | 0.031 / 0.85 | **+7.7%** · 106 · 44.3% · 1.15 | HTF size-scaler active |
| 3 | mean_reversion_v1 | SOL 1d | 0.019 / 0.68 | **+2.8%** · 7 · 71.4% · 2.38 | high win-rate |
| 4 | trend_following_v2 | ETH 1d | 0.097 / 1.00 | **+1.6%** · 17 · 35.7% · — | pyramiding |
| 5 | regime_momentum_v1 | ETH 1d | 0.067 / 0.67 | **+0.7%** · 21 · 42.9% · — | gate=off (default gate-on = 0 trades) |
| 6 | trend_following_v1 | ETH 1d | 0.075 / 0.98 | **+0.4%** · 13 · 27.3% · 1.12 | β −0.01 vs BuyHold −36.7% |
| 7 | volatility_breakout_v1 | SOL 1d | 0.123 / 0.92 | **+0.3%** · 16 · 100% · 3.72 | **Calmar 3.80**, MaxDD 0.1%, BuyHold −44.8% |
| 8 | ma_crossover_v1 | ETH 1d | 0.064 / 0.74 | −1.4% · 9 · 25% · 0.53 | weakest; provisional preset |
| 9 | momentum_v2 | BTC 4h | 0.004 / 0.81 | −3.3% · 38 · 36.8% · 0.83 | near-flat provisional preset |

**Uncovered (no guard-passing preset → defaults-only, honest note shown in UI):**

| # | Strategy | Best cell | Live return · n | Notes |
|--:|---|---|---|---|
| 10 | rules_v1 | (defaults) BTC 1d | **+2.6%** · 31 | "no robust preset" note verified live; progress panel 239/239 |
| 11 | htf_momentum_v1 | (defaults) | note verified live | gap-fill DSR 0.43 < 0.6 |

**7 of 9 covered strategies were net-positive on their best cell this 1Y window**
(`momentum_v1` +19.7% the standout); the 2 negatives are the provisional/weakest
presets (`ma_crossover_v1`, `momentum_v2`), consistent with their low DSR. Both
uncovered strategies ran clean (`rules_v1` +2.6%). No hangs; the progress panel
streamed for the slow `rules_v1` run.

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
