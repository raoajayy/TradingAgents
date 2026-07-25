# Strategy Lab — Portfolio Combination (Phase 5)

_Generated 2026-07-25T07:47:09.406433+00:00 · equal-weight blend of 6 diversified daily-crypto preset strategies · params fixed from the walk-forward presets (no new fitting) · annualization 365/yr._

## Blended portfolio vs best single component

| | Ann. Sharpe | Sortino | Max DD | Total return |
| --- | --- | --- | --- | --- |
| **Equal-weight portfolio** | **2.598** | 6.026 | 0.66% | +9.44% |
| Best single (volatility_breakout_v1@SOL-USD/1d) | 2.821 | — | 0.15% | — |

Diversification effect: the blend's Sharpe is **2.598** vs the best single component's **2.821** (-8%).

## Components (own active bars)

| Component | Ann. Sharpe | Max DD | Return-bars |
| --- | --- | --- | --- |
| volatility_breakout_v1@SOL-USD/1d | 2.821 | 0.15% | 777 |
| volatility_breakout_v1@ETH-USD/1d | 2.806 | 0.38% | 839 |
| trend_following_v2@ETH-USD/1d | 1.726 | 1.93% | 839 |
| trend_following_v1@ETH-USD/1d | 1.558 | 1.47% | 839 |
| mean_reversion_v1@SOL-USD/1d | 1.095 | 2.73% | 777 |
| trend_following_v2@SOL-USD/1d | 0.939 | 1.57% | 777 |

## Pairwise return correlation (aligned, flat-filled)

| | C0 | C1 | C2 | C3 | C4 | C5 |
| --- | --- | --- | --- | --- | --- | --- |
| C0 trend_following_v2@ETH-USD/1d | 1.00 | 0.61 | 0.92 | -0.13 | 0.28 | 0.12 |
| C1 volatility_breakout_v1@ETH-USD/1d | 0.61 | 1.00 | 0.60 | -0.09 | 0.17 | 0.15 |
| C2 trend_following_v1@ETH-USD/1d | 0.92 | 0.60 | 1.00 | -0.11 | 0.27 | 0.15 |
| C3 mean_reversion_v1@SOL-USD/1d | -0.13 | -0.09 | -0.11 | 1.00 | -0.22 | 0.01 |
| C4 trend_following_v2@SOL-USD/1d | 0.28 | 0.17 | 0.27 | -0.22 | 1.00 | 0.10 |
| C5 volatility_breakout_v1@SOL-USD/1d | 0.12 | 0.15 | 0.15 | 0.01 | 0.10 | 1.00 |

Average pairwise correlation: **0.19** (lower = more diversification benefit).

## Vol-targeted deployment

The blend's realized annualized volatility at base (1×) sizing is only **1.51%** — the strategies barely use their risk budget, which is why the absolute return is small despite the high Sharpe. Sharpe is scale-invariant, so sizing the portfolio to a target vol scales return AND drawdown by the same leverage factor:

| Target ann. vol | Leverage× | Ann. return | Max DD | Total return |
| --- | --- | --- | --- | --- |
| 5% | 3.3× | +13.7% | 2.2% | +34.4% |
| 10% | 6.6× | +29.0% | 4.3% | +79.7% |
| 15% | 9.9× | +46.0% | 6.4% | +138.8% |
| 20% | 13.2× | +64.9% | 8.5% | +215.7% |
| 25% | 16.5× | +85.7% | 10.5% | +315.1% |

_Leverage is the linear scale factor over base (fixed-risk) sizing; on crypto perps this is reachable within exchange limits. **Caveat:** linear scaling does NOT capture the extra funding cost, slippage, and liquidation risk that real leverage adds — treat higher-vol rows as an upper bound, and validate at the intended size before trusting them._

_Params fixed from the walk-forward-selected presets; this is a portfolio backtest at those params over the cached daily history, not a new optimization. Edges are modest and the sample is one crypto regime — treat as indicative, not a guaranteed forward result._

