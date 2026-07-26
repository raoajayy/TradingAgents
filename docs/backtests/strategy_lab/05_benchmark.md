# Strategy Lab — benchmark vs buy-&-hold (SO-A)

Each shipped preset measured **against simply holding the asset** over the same full window. `alpha`/`beta` are from an OLS fit of the strategy's per-bar returns on the buy-&-hold returns; `total_return` is the strategy, `benchmark_total_return` is buy-&-hold. A strategy earns its complexity only if it beats buy-&-hold on a risk-adjusted basis (positive alpha, lower drawdown) — not necessarily on raw return.

Source: `report.extended_report` over one full-window backtest per preset (`scripts/pro_lab_robustness.py`).

| Strategy | Sym | TF | Strat ret | Buy&Hold | Alpha | Beta | CAGR | Calmar | Recovery | Risk-of-ruin |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| htf_momentum_v2 | ETH-USD | 4h | +12.8% | -34.1% | +0.0514 | -0.02 | +5.1% | 1.53 | 3.37 | 0.0% |
| ma_crossover_v1 | ETH-USD | 1d | +3.9% | -44.5% | +0.0169 | -0.00 | +1.7% | 0.50 | 1.09 | 0.0% |
| mean_reversion_v1 | BTC-USD | 4h | +8.8% | +50.0% | +0.0286 | 0.02 | +3.4% | 1.25 | 2.98 | 0.0% |
| mean_reversion_v1 | SOL-USD | 1d | +8.1% | -54.5% | +0.0375 | 0.01 | +3.7% | 1.36 | 2.74 | 0.0% |
| momentum_v1 | ETH-USD | 4h | +14.6% | -34.1% | +0.0584 | -0.02 | +5.8% | 0.80 | 1.74 | 0.0% |
| momentum_v2 | BTC-USD | 4h | +7.8% | +50.0% | +0.0288 | 0.00 | +3.0% | 1.45 | 3.46 | 0.0% |
| regime_momentum_v1 | ETH-USD | 1d | +8.4% | -44.5% | +0.0363 | -0.01 | +3.6% | 0.67 | 1.45 | 0.0% |
| regime_momentum_v1 | ETH-USD | 4h | +21.9% | -34.1% | +0.0832 | -0.01 | +8.5% | 2.37 | 4.95 | 0.0% |
| trend_following_v1 | ETH-USD | 1d | +10.6% | -44.5% | +0.0441 | -0.00 | +4.5% | 3.05 | 6.49 | 0.0% |
| trend_following_v1 | SOL-USD | 4h | +8.9% | -47.0% | +0.0387 | 0.00 | +3.8% | 1.01 | 2.11 | 0.0% |
| trend_following_v2 | ETH-USD | 1d | +17.2% | -44.5% | +0.0698 | 0.00 | +7.1% | 3.69 | 7.55 | 0.0% |
| trend_following_v2 | ETH-USD | 4h | +17.0% | -34.1% | +0.0655 | 0.01 | +6.7% | 1.35 | 2.82 | 0.0% |
| trend_following_v2 | SOL-USD | 1d | +4.5% | -54.5% | +0.0209 | -0.00 | +2.1% | 1.33 | 2.74 | 0.0% |
| trend_following_v2 | SOL-USD | 4h | +5.4% | -47.0% | +0.0238 | 0.00 | +2.3% | 0.53 | 1.12 | 0.0% |
| volatility_breakout_v1 | ETH-USD | 1d | +13.7% | -44.5% | +0.0559 | -0.00 | +5.7% | 15.21 | 31.90 | 0.0% |
| volatility_breakout_v1 | ETH-USD | 1h | +2.9% | -23.2% | +0.0132 | -0.00 | +1.1% | 0.17 | 0.42 | 0.0% |
| volatility_breakout_v1 | ETH-USD | 4h | +46.2% | -34.1% | +0.1576 | -0.00 | +16.9% | 3.54 | 6.52 | 0.0% |
| volatility_breakout_v1 | SOL-USD | 1d | +2.9% | -54.5% | +0.0136 | 0.00 | +1.4% | 9.05 | 18.85 | 0.0% |
| volatility_breakout_v1 | SOL-USD | 4h | +38.4% | -47.0% | +0.1450 | -0.00 | +15.4% | 4.62 | 8.27 | 0.0% |

**Reading it:** these presets are deliberately **low-beta, positive-alpha** — they are in the market a fraction of the time (most are breakout/trend systems that sit flat between signals), so raw return is usually *below* buy-&-hold in a roaring bull window, while alpha and Calmar (return per unit of max drawdown) are the honest edge. A near-zero beta with positive alpha is exactly the uncorrelated return stream the portfolio layer (02_portfolio.md) then combines.
