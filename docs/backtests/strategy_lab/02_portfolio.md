# Strategy Lab — Portfolio Combination (Phase 5)

_Generated 2026-07-25T07:29:19.935682+00:00 · equal-weight blend of 6 diversified daily-crypto preset strategies · params fixed from the walk-forward presets (no new fitting) · annualization 365/yr._

## Blended portfolio vs best single component

| | Ann. Sharpe | Sortino | Max DD | Total return |
| --- | --- | --- | --- | --- |
| **Equal-weight portfolio** | **2.216** | 4.911 | 0.78% | +8.93% |
| Best single (volatility_breakout_v1@ETH-USD/1d) | 1.749 | — | 1.36% | — |

Diversification effect: the blend's Sharpe is **2.216** vs the best single component's **1.749** (+27%).

## Components (own active bars)

| Component | Ann. Sharpe | Max DD | Return-bars |
| --- | --- | --- | --- |
| volatility_breakout_v1@ETH-USD/1d | 1.749 | 1.36% | 839 |
| trend_following_v2@ETH-USD/1d | 1.726 | 1.93% | 839 |
| trend_following_v1@ETH-USD/1d | 1.558 | 1.47% | 839 |
| mean_reversion_v1@SOL-USD/1d | 1.095 | 2.73% | 777 |
| volatility_breakout_v1@SOL-USD/1d | 0.999 | 0.94% | 777 |
| trend_following_v2@SOL-USD/1d | 0.939 | 1.57% | 777 |

## Pairwise return correlation (aligned, flat-filled)

| | C0 | C1 | C2 | C3 | C4 | C5 |
| --- | --- | --- | --- | --- | --- | --- |
| C0 trend_following_v2@ETH-USD/1d | 1.00 | 0.77 | 0.92 | -0.13 | 0.28 | 0.28 |
| C1 volatility_breakout_v1@ETH-USD/1d | 0.77 | 1.00 | 0.76 | -0.08 | 0.12 | 0.26 |
| C2 trend_following_v1@ETH-USD/1d | 0.92 | 0.76 | 1.00 | -0.11 | 0.27 | 0.26 |
| C3 mean_reversion_v1@SOL-USD/1d | -0.13 | -0.08 | -0.11 | 1.00 | -0.22 | -0.08 |
| C4 trend_following_v2@SOL-USD/1d | 0.28 | 0.12 | 0.27 | -0.22 | 1.00 | 0.31 |
| C5 volatility_breakout_v1@SOL-USD/1d | 0.28 | 0.26 | 0.26 | -0.08 | 0.31 | 1.00 |

Average pairwise correlation: **0.24** (lower = more diversification benefit).

_Params fixed from the walk-forward-selected presets; this is a portfolio backtest at those params over the cached daily history, not a new optimization. Edges are modest and the sample is one crypto regime — treat as indicative, not a guaranteed forward result._

