# Strategy Lab — Portfolio Combination (Phase 5)

_Generated 2026-07-25T07:56:26.793846+00:00 · blend of 6 diversified daily-crypto preset strategies · params fixed from the walk-forward presets (no new fitting) · annualization 365/yr._

## Allocation schemes (OOS-validated)

Weights fit on the first 60% of history, measured on the held-out last 40%. The **best out-of-sample** scheme is chosen for the headline — a return-tilt only wins if it generalizes, else risk-parity/equal wins.

| Scheme | Full-sample Sharpe | OOS (held-out) Sharpe |
| --- | --- | --- |
| equal | 2.598 | 1.930 |
| inverse-vol ✅ chosen | 3.155 | 2.412 |
| sharpe-tilt | 2.710 | 1.790 |

Chosen allocation (**inverse-vol**) weights: trend_following_v2@ETH-USD/1d 6%, volatility_breakout_v1@ETH-USD/1d 12%, trend_following_v1@ETH-USD/1d 9%, mean_reversion_v1@SOL-USD/1d 8%, trend_following_v2@SOL-USD/1d 12%, volatility_breakout_v1@SOL-USD/1d 54%

## Blended portfolio vs best single component

| | Ann. Sharpe | Sortino | Max DD | Total return |
| --- | --- | --- | --- | --- |
| **Portfolio (inverse-vol)** | **3.155** | 7.864 | 0.30% | +6.31% |
| Best single (volatility_breakout_v1@SOL-USD/1d) | 2.821 | — | 0.15% | — |

Diversification effect: the blend's Sharpe is **3.155** vs the best single component's **2.821** (+12%).

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

The blend's realized annualized volatility at base (1×) sizing is only **0.84%** — the strategies barely use their risk budget, which is why the absolute return is small despite the high Sharpe. Sharpe is scale-invariant, so sizing the portfolio to a target vol scales return AND drawdown by the same leverage factor:

| Target ann. vol | Leverage× | Ann. return | Max DD | Total return |
| --- | --- | --- | --- | --- |
| 5% | 5.9× | +17.0% | 1.8% | +43.3% |
| 10% | 11.9× | +36.4% | 3.5% | +104.2% |
| 15% | 17.8× | +58.8% | 5.2% | +189.4% |
| 20% | 23.7× | +84.3% | 6.9% | +307.7% |
| 25% | 29.6× | +113.4% | 8.5% | +471.4% |

_Leverage is the linear scale factor over base (fixed-risk) sizing; on crypto perps this is reachable within exchange limits. **Caveat:** linear scaling does NOT capture the extra funding cost, slippage, and liquidation risk that real leverage adds — treat higher-vol rows as an upper bound, and validate at the intended size before trusting them._

_Params fixed from the walk-forward-selected presets; this is a portfolio backtest at those params over the cached daily history, not a new optimization. Edges are modest and the sample is one crypto regime — treat as indicative, not a guaranteed forward result._

