# Strategy Lab — Portfolio Combination (Phase 5)

_Generated 2026-07-25T07:35:45.133277+00:00 · equal-weight blend of 6 diversified daily-crypto preset strategies · params fixed from the walk-forward presets (no new fitting) · annualization 365/yr._

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

## Vol-targeted deployment

The blend's realized annualized volatility at base (1×) sizing is only **1.68%** — the strategies barely use their risk budget, which is why the absolute return is small despite the high Sharpe. Sharpe is scale-invariant, so sizing the portfolio to a target vol scales return AND drawdown by the same leverage factor:

| Target ann. vol | Leverage× | Ann. return | Max DD | Total return |
| --- | --- | --- | --- | --- |
| 5% | 3.0× | +11.6% | 2.3% | +28.7% |
| 10% | 5.9× | +24.2% | 4.6% | +64.6% |
| 15% | 8.9× | +37.9% | 6.8% | +109.4% |
| 20% | 11.9× | +52.8% | 9.0% | +164.9% |
| 25% | 14.8× | +68.8% | 11.1% | +233.3% |

_Leverage is the linear scale factor over base (fixed-risk) sizing; on crypto perps this is reachable within exchange limits. **Caveat:** linear scaling does NOT capture the extra funding cost, slippage, and liquidation risk that real leverage adds — treat higher-vol rows as an upper bound, and validate at the intended size before trusting them._

_Params fixed from the walk-forward-selected presets; this is a portfolio backtest at those params over the cached daily history, not a new optimization. Edges are modest and the sample is one crypto regime — treat as indicative, not a guaranteed forward result._

