# Strategy Lab — Findings (Phases 1-2)

_Generated 2026-07-26T08:34:59.812446+00:00 · objective `sharpe` · walk-forward OOS headline + DSR/PBO guard bar (pass = DSR≥0.6 & PBO≤0.5 & OOS-Sharpe>0)._

**12 / 30 scored cells pass the guard bar** (0 cells were data-limited and not scored).

## Leaderboard — guard-passing cells (by OOS Sharpe)

| Strategy | Symbol | TF | OOS Sharpe | DSR | PBO | Trials | Stable params |
| --- | --- | --- | --- | --- | --- | --- | --- |
| trend_following_v2 | ETH-USD | 1d | 0.097 | 0.896 | 0.21 | 12 | `{"donchian_period": 20, "max_adds": 2, "trail_mode": "pct"}` |
| volatility_breakout_v1 | ETH-USD | 1d | 0.083 | 0.903 | 0.04 | 12 | `{"lookback": 20, "squeeze_pct": 0.08, "trail_mode": "pct"}` |
| trend_following_v1 | ETH-USD | 1d | 0.075 | 0.903 | 0.44 | 12 | `{"donchian_period": 20, "stop_atr_mult": 2.0, "trail_mode": "pct"}` |
| volatility_breakout_v1 | SOL-USD | 4h | 0.070 | 0.990 | 0.19 | 12 | `{"lookback": 30, "squeeze_pct": 0.05, "trail_mode": "chandelier"}` |
| ma_crossover_v1 | ETH-USD | 1d | 0.064 | 0.737 | 0.41 | 12 | `{"adx_filter": "off", "fast_period": 8, "slow_period": 30}` |
| volatility_breakout_v1 | ETH-USD | 4h | 0.054 | 0.993 | 0.01 | 12 | `{"lookback": 20, "squeeze_pct": 0.03, "trail_mode": "chandelier"}` |
| trend_following_v2 | ETH-USD | 4h | 0.028 | 0.874 | 0.19 | 12 | `{"donchian_period": 40, "max_adds": 2, "trail_mode": "pct"}` |
| htf_momentum_v2 | ETH-USD | 4h | 0.028 | 0.834 | 0.28 | 9 | `{"roc_period": 14, "roc_threshold": 3.0}` |
| volatility_breakout_v1 | SOL-USD | 1d | 0.017 | 0.967 | 0.00 | 12 | `{"lookback": 20, "squeeze_pct": 0.08, "trail_mode": "chandelier"}` |
| volatility_breakout_v1 | BTC-USD | 4h | 0.009 | 0.894 | 0.04 | 12 | `{"lookback": 30, "squeeze_pct": 0.03, "trail_mode": "chandelier"}` |
| trend_following_v1 | SOL-USD | 4h | 0.004 | 0.706 | 0.21 | 12 | `{"donchian_period": 50, "stop_atr_mult": 2.0, "trail_mode": "chandelier"}` |
| trend_following_v1 | ETH-USD | 4h | 0.002 | 0.940 | 0.02 | 12 | `{"donchian_period": 50, "stop_atr_mult": 2.0, "trail_mode": "pct"}` |

## All scored cells

| Strategy | Symbol | TF | Bars | Wins | OOS Sharpe | DSR | PBO | Guard | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| htf_momentum_v2 | BTC-USD | 1d | 939 | 2 | -0.020 | 0.607 | 0.24 | — | survives the overfitting gauntlet (PBO 0.24, deflated Sharpe |
| htf_momentum_v2 | BTC-USD | 4h | 4000 | 4 | 0.007 | 0.634 | 0.69 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.69, |
| htf_momentum_v2 | ETH-USD | 1d | 900 | 2 | 0.081 | 0.687 | 0.75 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.75, |
| htf_momentum_v2 | ETH-USD | 4h | 4000 | 4 | 0.028 | 0.834 | 0.28 | ✅ | survives the overfitting gauntlet (PBO 0.28, deflated Sharpe |
| htf_momentum_v2 | SOL-USD | 1d | 838 | 2 | 0.067 | 0.526 | 0.82 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.82, |
| htf_momentum_v2 | SOL-USD | 4h | 4000 | 4 | -0.021 | 0.683 | 0.81 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.81, |
| ma_crossover_v1 | BTC-USD | 1d | 939 | 2 | -0.026 | 0.551 | 0.49 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.49, |
| ma_crossover_v1 | BTC-USD | 4h | 4000 | 4 | -0.013 | 0.645 | 0.34 | — | survives the overfitting gauntlet (PBO 0.34, deflated Sharpe |
| ma_crossover_v1 | ETH-USD | 1d | 900 | 2 | 0.064 | 0.737 | 0.41 | ✅ | survives the overfitting gauntlet (PBO 0.41, deflated Sharpe |
| ma_crossover_v1 | ETH-USD | 4h | 4000 | 4 | -0.002 | 0.906 | 0.35 | — | survives the overfitting gauntlet (PBO 0.35, deflated Sharpe |
| ma_crossover_v1 | SOL-USD | 1d | 838 | 2 | -0.018 | 0.760 | 0.29 | — | survives the overfitting gauntlet (PBO 0.29, deflated Sharpe |
| ma_crossover_v1 | SOL-USD | 4h | 4000 | 4 | -0.017 | 0.710 | 0.67 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.67, |
| trend_following_v1 | BTC-USD | 1d | 939 | 2 | -0.007 | 0.569 | 0.94 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.94, |
| trend_following_v1 | BTC-USD | 4h | 4000 | 4 | -0.008 | 0.295 | 0.99 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.99, |
| trend_following_v1 | ETH-USD | 1d | 900 | 2 | 0.075 | 0.903 | 0.44 | ✅ | survives the overfitting gauntlet (PBO 0.44, deflated Sharpe |
| trend_following_v1 | ETH-USD | 4h | 4000 | 4 | 0.002 | 0.940 | 0.02 | ✅ | survives the overfitting gauntlet (PBO 0.02, deflated Sharpe |
| trend_following_v1 | SOL-USD | 1d | 838 | 2 | 0.055 | 0.530 | 0.68 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.68, |
| trend_following_v1 | SOL-USD | 4h | 4000 | 4 | 0.004 | 0.706 | 0.21 | ✅ | survives the overfitting gauntlet (PBO 0.21, deflated Sharpe |
| trend_following_v2 | BTC-USD | 1d | 939 | 2 | -0.066 | 0.614 | 0.63 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.63, |
| trend_following_v2 | BTC-USD | 4h | 4000 | 4 | -0.008 | 0.399 | 0.74 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.74, |
| trend_following_v2 | ETH-USD | 1d | 900 | 2 | 0.097 | 0.896 | 0.21 | ✅ | survives the overfitting gauntlet (PBO 0.21, deflated Sharpe |
| trend_following_v2 | ETH-USD | 4h | 4000 | 4 | 0.028 | 0.874 | 0.19 | ✅ | survives the overfitting gauntlet (PBO 0.19, deflated Sharpe |
| trend_following_v2 | SOL-USD | 1d | 838 | 2 | 0.057 | 0.639 | 0.60 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.60, |
| trend_following_v2 | SOL-USD | 4h | 4000 | 4 | -0.001 | 0.707 | 0.33 | — | survives the overfitting gauntlet (PBO 0.33, deflated Sharpe |
| volatility_breakout_v1 | BTC-USD | 1d | 939 | 2 | -0.069 | 0.674 | 0.18 | — | survives the overfitting gauntlet (PBO 0.18, deflated Sharpe |
| volatility_breakout_v1 | BTC-USD | 4h | 4000 | 4 | 0.009 | 0.894 | 0.04 | ✅ | survives the overfitting gauntlet (PBO 0.04, deflated Sharpe |
| volatility_breakout_v1 | ETH-USD | 1d | 900 | 2 | 0.083 | 0.903 | 0.04 | ✅ | survives the overfitting gauntlet (PBO 0.04, deflated Sharpe |
| volatility_breakout_v1 | ETH-USD | 4h | 4000 | 4 | 0.054 | 0.993 | 0.01 | ✅ | survives the overfitting gauntlet (PBO 0.01, deflated Sharpe |
| volatility_breakout_v1 | SOL-USD | 1d | 838 | 2 | 0.017 | 0.967 | 0.00 | ✅ | survives the overfitting gauntlet (PBO 0.00, deflated Sharpe |
| volatility_breakout_v1 | SOL-USD | 4h | 4000 | 4 | 0.070 | 0.990 | 0.19 | ✅ | survives the overfitting gauntlet (PBO 0.19, deflated Sharpe |

_Full per-cell metrics + chosen params in `results.json`._

