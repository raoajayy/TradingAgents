# Strategy Lab — Findings (Phases 1-2)

_Generated 2026-07-25T06:56:52.771808+00:00 · objective `sharpe` · walk-forward OOS headline + DSR/PBO guard bar (pass = DSR≥0.6 & PBO≤0.5 & OOS-Sharpe>0)._

**16 / 181 scored cells pass the guard bar** (43 cells were data-limited and not scored).

## Leaderboard — guard-passing cells (by OOS Sharpe)

| Strategy | Symbol | TF | OOS Sharpe | DSR | PBO | Trials | Stable params |
| --- | --- | --- | --- | --- | --- | --- | --- |
| trend_following_v2 | ETH-USD | 1d | 0.097 | 0.998 | 0.15 | 8 | `{"add_atr_mult": 1.0, "donchian_period": 20, "max_adds": 2}` |
| volatility_breakout_v1 | ETH-USD | 1d | 0.083 | 0.937 | 0.01 | 6 | `{"lookback": 20, "squeeze_pct": 0.08}` |
| trend_following_v1 | ETH-USD | 1d | 0.075 | 0.980 | 0.21 | 8 | `{"donchian_period": 20, "stop_atr_mult": 2.0, "trail_pct": 0.05}` |
| regime_momentum_v1 | ETH-USD | 1d | 0.067 | 0.668 | 0.31 | 8 | `{"regime_gate": "off", "roc_period": 20, "roc_threshold": 5.0}` |
| trend_following_v2 | SOL-USD | 1d | 0.057 | 0.765 | 0.14 | 8 | `{"add_atr_mult": 2.0, "donchian_period": 40, "max_adds": 2}` |
| regime_momentum_v1 | ETH-USD | 4h | 0.040 | 0.917 | 0.04 | 8 | `{"regime_gate": "on", "roc_period": 10, "roc_threshold": 3.0}` |
| trend_following_v2 | ETH-USD | 4h | 0.037 | 0.904 | 0.07 | 8 | `{"add_atr_mult": 2.0, "donchian_period": 40, "max_adds": 2}` |
| volatility_breakout_v1 | ETH-USD | 4h | 0.034 | 0.919 | 0.18 | 6 | `{"lookback": 30, "squeeze_pct": 0.08}` |
| volatility_breakout_v1 | SOL-USD | 1d | 0.030 | 0.810 | 0.25 | 6 | `{"lookback": 20, "squeeze_pct": 0.08}` |
| momentum_v1 | ETH-USD | 4h | 0.025 | 0.854 | 0.12 | 9 | `{"roc_period": 14, "roc_threshold": 3.0}` |
| trend_following_v1 | SOL-USD | 4h | 0.024 | 0.771 | 0.08 | 8 | `{"donchian_period": 50, "stop_atr_mult": 2.0, "trail_pct": 0.08}` |
| mean_reversion_v1 | SOL-USD | 1d | 0.019 | 0.681 | 0.26 | 8 | `{"entry_std": 2.5, "lookback": 30, "stop_atr_mult": 2.0}` |
| trend_following_v2 | SOL-USD | 4h | 0.018 | 0.668 | 0.13 | 8 | `{"add_atr_mult": 1.0, "donchian_period": 40, "max_adds": 0}` |
| volatility_breakout_v1 | SOL-USD | 4h | 0.016 | 0.901 | 0.38 | 6 | `{"lookback": 30, "squeeze_pct": 0.05}` |
| volatility_breakout_v1 | ETH-USD | 1h | 0.007 | 0.658 | 0.42 | 6 | `{"lookback": 20, "squeeze_pct": 0.03}` |
| mean_reversion_v1 | BTC-USD | 4h | 0.002 | 0.731 | 0.38 | 8 | `{"entry_std": 2.5, "lookback": 30, "stop_atr_mult": 2.0}` |

## All scored cells

| Strategy | Symbol | TF | Bars | Wins | OOS Sharpe | DSR | PBO | Guard | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| htf_momentum_v1 | BTC-USD | 15m | 4000 | 4 | 0.000 | 0.172 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| htf_momentum_v1 | BTC-USD | 1h | 4000 | 4 | 0.001 | 0.143 | 0.72 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.72, |
| htf_momentum_v1 | BTC-USD | 30m | 4000 | 4 | -0.024 | 0.125 | 0.58 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.58, |
| htf_momentum_v1 | BTC-USD | 4h | 4000 | 4 | 0.005 | 0.567 | 0.17 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.17, |
| htf_momentum_v1 | BTC-USD | 5m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| htf_momentum_v1 | ETH-USD | 15m | 4000 | 4 | -0.050 | 0.296 | 0.61 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.61, |
| htf_momentum_v1 | ETH-USD | 1h | 4000 | 4 | -0.024 | 0.191 | 0.55 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.55, |
| htf_momentum_v1 | ETH-USD | 30m | 4000 | 4 | 0.002 | 0.384 | 0.55 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.55, |
| htf_momentum_v1 | ETH-USD | 4h | 4000 | 4 | 0.029 | 0.655 | 0.54 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.54, |
| htf_momentum_v1 | ETH-USD | 5m | 4000 | 4 | 0.000 | 0.524 | 0.78 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.78, |
| htf_momentum_v1 | SOL-USD | 15m | 4000 | 4 | -0.008 | 0.191 | 0.60 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.60, |
| htf_momentum_v1 | SOL-USD | 1h | 4000 | 4 | -0.008 | 0.417 | 0.59 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.59, |
| htf_momentum_v1 | SOL-USD | 30m | 4000 | 4 | -0.002 | 0.079 | 0.98 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.98, |
| htf_momentum_v1 | SOL-USD | 4h | 4000 | 4 | -0.022 | 0.447 | 0.40 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.40, |
| htf_momentum_v1 | SOL-USD | 5m | 4000 | 4 | 0.000 | 0.199 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| htf_momentum_v1 | XAUUSD | 15m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| htf_momentum_v1 | XAUUSD | 1h | 2360 | 2 | -0.059 | 0.714 | 0.36 | — | survives the overfitting gauntlet (PBO 0.36, deflated Sharpe |
| htf_momentum_v1 | XAUUSD | 30m | 4000 | 4 | -0.011 | 0.626 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| htf_momentum_v1 | XAUUSD | 4h | 590 | 2 | 0.079 | 0.298 | 0.87 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.87, |
| htf_momentum_v1 | XAUUSD | 5m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| ma_crossover_v1 | BTC-USD | 15m | 4000 | 4 | 0.000 | 0.117 | 0.17 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.17, |
| ma_crossover_v1 | BTC-USD | 1d | 939 | 2 | -0.006 | 0.586 | 0.63 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.63, |
| ma_crossover_v1 | BTC-USD | 1h | 4000 | 4 | -0.052 | 0.298 | 0.46 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.46, |
| ma_crossover_v1 | BTC-USD | 30m | 4000 | 4 | -0.027 | 0.288 | 0.79 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.79, |
| ma_crossover_v1 | BTC-USD | 4h | 4000 | 4 | -0.002 | 0.591 | 0.31 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.31, |
| ma_crossover_v1 | BTC-USD | 5m | 4000 | 4 | 0.000 | 0.174 | 0.37 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.37, |
| ma_crossover_v1 | ETH-USD | 15m | 4000 | 4 | 0.000 | 0.207 | 0.32 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.32, |
| ma_crossover_v1 | ETH-USD | 1d | 900 | 2 | 0.064 | 0.818 | 0.53 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.53, |
| ma_crossover_v1 | ETH-USD | 1h | 4000 | 4 | -0.041 | 0.235 | 0.61 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.61, |
| ma_crossover_v1 | ETH-USD | 30m | 4000 | 4 | -0.010 | 0.499 | 0.69 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.69, |
| ma_crossover_v1 | ETH-USD | 4h | 4000 | 4 | 0.019 | 0.775 | 0.68 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.68, |
| ma_crossover_v1 | ETH-USD | 5m | 4000 | 4 | -0.029 | 0.404 | 0.64 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.64, |
| ma_crossover_v1 | SOL-USD | 15m | 4000 | 4 | 0.010 | 0.273 | 0.15 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.15, |
| ma_crossover_v1 | SOL-USD | 1d | 838 | 2 | -0.039 | 0.657 | 0.37 | — | survives the overfitting gauntlet (PBO 0.37, deflated Sharpe |
| ma_crossover_v1 | SOL-USD | 1h | 4000 | 4 | 0.008 | 0.619 | 0.81 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.81, |
| ma_crossover_v1 | SOL-USD | 30m | 4000 | 4 | -0.004 | 0.650 | 0.59 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.59, |
| ma_crossover_v1 | SOL-USD | 4h | 4000 | 4 | -0.017 | 0.760 | 0.70 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.70, |
| ma_crossover_v1 | SOL-USD | 5m | 4000 | 4 | 0.009 | 0.234 | 0.49 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.49, |
| ma_crossover_v1 | XAUUSD | 15m | 4000 | 4 | -0.043 | 0.314 | 0.94 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.94, |
| ma_crossover_v1 | XAUUSD | 1h | 2360 | 2 | 0.000 | 0.283 | 0.85 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.85, |
| ma_crossover_v1 | XAUUSD | 30m | 4000 | 4 | -0.004 | 0.657 | 0.71 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.71, |
| ma_crossover_v1 | XAUUSD | 4h | 590 | 2 | 0.066 | 0.576 | 0.67 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.67, |
| ma_crossover_v1 | XAUUSD | 5m | 4000 | 4 | -0.047 | 0.283 | 0.53 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.53, |
| mean_reversion_v1 | BTC-USD | 15m | 4000 | 4 | -0.012 | 0.361 | 0.67 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.67, |
| mean_reversion_v1 | BTC-USD | 1d | 939 | 2 | 0.085 | 0.331 | 0.65 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.65, |
| mean_reversion_v1 | BTC-USD | 1h | 4000 | 4 | 0.002 | 0.295 | 0.35 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.35, |
| mean_reversion_v1 | BTC-USD | 30m | 4000 | 4 | 0.014 | 0.350 | 0.64 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.64, |
| mean_reversion_v1 | BTC-USD | 4h | 4000 | 4 | 0.002 | 0.731 | 0.38 | ✅ | survives the overfitting gauntlet (PBO 0.38, deflated Sharpe |
| mean_reversion_v1 | BTC-USD | 5m | 4000 | 4 | -0.048 | 0.004 | 0.88 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.88, |
| mean_reversion_v1 | ETH-USD | 15m | 4000 | 4 | -0.013 | 0.418 | 0.23 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.23, |
| mean_reversion_v1 | ETH-USD | 1d | 900 | 2 | -0.002 | 0.250 | 0.26 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.26, |
| mean_reversion_v1 | ETH-USD | 1h | 4000 | 4 | -0.012 | 0.121 | 0.79 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.79, |
| mean_reversion_v1 | ETH-USD | 30m | 4000 | 4 | 0.024 | 0.554 | 0.62 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.62, |
| mean_reversion_v1 | ETH-USD | 4h | 4000 | 4 | -0.032 | 0.206 | 0.40 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.40, |
| mean_reversion_v1 | ETH-USD | 5m | 4000 | 4 | -0.027 | 0.051 | 0.92 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.92, |
| mean_reversion_v1 | SOL-USD | 15m | 4000 | 4 | -0.025 | 0.308 | 0.11 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.11, |
| mean_reversion_v1 | SOL-USD | 1d | 838 | 2 | 0.019 | 0.681 | 0.26 | ✅ | survives the overfitting gauntlet (PBO 0.26, deflated Sharpe |
| mean_reversion_v1 | SOL-USD | 1h | 4000 | 4 | -0.011 | 0.348 | 0.35 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.35, |
| mean_reversion_v1 | SOL-USD | 30m | 4000 | 4 | 0.001 | 0.270 | 0.93 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.93, |
| mean_reversion_v1 | SOL-USD | 4h | 4000 | 4 | -0.040 | 0.139 | 0.14 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.14, |
| mean_reversion_v1 | SOL-USD | 5m | 4000 | 4 | 0.021 | 0.250 | 0.31 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.31, |
| mean_reversion_v1 | XAUUSD | 15m | 4000 | 4 | -0.028 | 0.082 | 0.22 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.22, |
| mean_reversion_v1 | XAUUSD | 1h | 2360 | 2 | -0.051 | 0.110 | 0.70 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.70, |
| mean_reversion_v1 | XAUUSD | 30m | 4000 | 4 | -0.040 | 0.019 | 0.40 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.40, |
| mean_reversion_v1 | XAUUSD | 4h | 590 | 2 | -0.052 | 0.664 | 0.68 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.68, |
| mean_reversion_v1 | XAUUSD | 5m | 4000 | 4 | -0.029 | 0.004 | 0.07 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.07, |
| momentum_v1 | BTC-USD | 15m | 4000 | 4 | 0.000 | 0.210 | 0.86 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.86, |
| momentum_v1 | BTC-USD | 1d | 939 | 2 | -0.038 | 0.653 | 0.39 | — | survives the overfitting gauntlet (PBO 0.39, deflated Sharpe |
| momentum_v1 | BTC-USD | 1h | 4000 | 4 | -0.004 | 0.192 | 0.68 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.68, |
| momentum_v1 | BTC-USD | 30m | 4000 | 4 | -0.006 | 0.087 | 0.87 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.87, |
| momentum_v1 | BTC-USD | 4h | 4000 | 4 | 0.015 | 0.567 | 0.89 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.89, |
| momentum_v1 | BTC-USD | 5m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| momentum_v1 | ETH-USD | 15m | 4000 | 4 | -0.048 | 0.191 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| momentum_v1 | ETH-USD | 1d | 900 | 2 | 0.067 | 0.646 | 0.69 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.69, |
| momentum_v1 | ETH-USD | 1h | 4000 | 4 | -0.007 | 0.256 | 0.83 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.83, |
| momentum_v1 | ETH-USD | 30m | 4000 | 4 | -0.006 | 0.587 | 0.98 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.98, |
| momentum_v1 | ETH-USD | 4h | 4000 | 4 | 0.025 | 0.854 | 0.12 | ✅ | survives the overfitting gauntlet (PBO 0.12, deflated Sharpe |
| momentum_v1 | ETH-USD | 5m | 4000 | 4 | 0.000 | 0.538 | 0.50 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.50, |
| momentum_v1 | SOL-USD | 15m | 4000 | 4 | -0.008 | 0.188 | 0.83 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.83, |
| momentum_v1 | SOL-USD | 1d | 838 | 2 | 0.065 | 0.493 | 0.68 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.68, |
| momentum_v1 | SOL-USD | 1h | 4000 | 4 | -0.029 | 0.194 | 0.72 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.72, |
| momentum_v1 | SOL-USD | 30m | 4000 | 4 | 0.014 | 0.163 | 0.80 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.80, |
| momentum_v1 | SOL-USD | 4h | 4000 | 4 | -0.036 | 0.470 | 0.87 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.87, |
| momentum_v1 | SOL-USD | 5m | 4000 | 4 | 0.000 | 0.199 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| momentum_v1 | XAUUSD | 15m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| momentum_v1 | XAUUSD | 1h | 2360 | 2 | -0.059 | 0.577 | 0.50 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.50, |
| momentum_v1 | XAUUSD | 30m | 4000 | 4 | -0.011 | 0.570 | 0.22 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.22, |
| momentum_v1 | XAUUSD | 4h | 590 | 2 | 0.026 | 0.238 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| momentum_v1 | XAUUSD | 5m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| regime_momentum_v1 | BTC-USD | 15m | 4000 | 4 | 0.000 | 0.217 | 0.62 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.62, |
| regime_momentum_v1 | BTC-USD | 1d | 939 | 2 | -0.042 | 0.536 | 0.38 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.38, |
| regime_momentum_v1 | BTC-USD | 1h | 4000 | 4 | -0.020 | 0.166 | 0.46 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.46, |
| regime_momentum_v1 | BTC-USD | 30m | 4000 | 4 | -0.013 | 0.130 | 0.75 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.75, |
| regime_momentum_v1 | BTC-USD | 4h | 4000 | 4 | 0.010 | 0.639 | 0.82 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.82, |
| regime_momentum_v1 | BTC-USD | 5m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| regime_momentum_v1 | ETH-USD | 15m | 4000 | 4 | 0.004 | 0.174 | 0.38 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.38, |
| regime_momentum_v1 | ETH-USD | 1d | 900 | 2 | 0.067 | 0.668 | 0.31 | ✅ | survives the overfitting gauntlet (PBO 0.31, deflated Sharpe |
| regime_momentum_v1 | ETH-USD | 1h | 4000 | 4 | -0.004 | 0.276 | 0.67 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.67, |
| regime_momentum_v1 | ETH-USD | 30m | 4000 | 4 | -0.013 | 0.453 | 0.83 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.83, |
| regime_momentum_v1 | ETH-USD | 4h | 4000 | 4 | 0.040 | 0.917 | 0.04 | ✅ | survives the overfitting gauntlet (PBO 0.04, deflated Sharpe |
| regime_momentum_v1 | ETH-USD | 5m | 4000 | 4 | 0.000 | 0.534 | 0.50 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.50, |
| regime_momentum_v1 | SOL-USD | 15m | 4000 | 4 | 0.012 | 0.111 | 0.67 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.67, |
| regime_momentum_v1 | SOL-USD | 1d | 838 | 2 | 0.020 | 0.550 | 0.63 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.63, |
| regime_momentum_v1 | SOL-USD | 1h | 4000 | 4 | 0.011 | 0.560 | 0.73 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.73, |
| regime_momentum_v1 | SOL-USD | 30m | 4000 | 4 | 0.014 | 0.237 | 0.46 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.46, |
| regime_momentum_v1 | SOL-USD | 4h | 4000 | 4 | -0.028 | 0.696 | 0.92 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.92, |
| regime_momentum_v1 | SOL-USD | 5m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| regime_momentum_v1 | XAUUSD | 15m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| regime_momentum_v1 | XAUUSD | 1h | 2360 | 2 | -0.059 | 0.594 | 0.64 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.64, |
| regime_momentum_v1 | XAUUSD | 30m | 4000 | 4 | 0.002 | 0.499 | 0.22 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.22, |
| regime_momentum_v1 | XAUUSD | 4h | 590 | 2 | 0.026 | 0.275 | 0.67 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.67, |
| regime_momentum_v1 | XAUUSD | 5m | 4000 | 4 | 0.000 | 0.500 | 1.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 1.00, |
| trend_following_v1 | BTC-USD | 15m | 4000 | 4 | -0.031 | 0.083 | 0.66 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.66, |
| trend_following_v1 | BTC-USD | 1d | 939 | 2 | -0.009 | 0.611 | 0.91 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.91, |
| trend_following_v1 | BTC-USD | 1h | 4000 | 4 | -0.002 | 0.239 | 0.95 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.95, |
| trend_following_v1 | BTC-USD | 30m | 4000 | 4 | -0.037 | 0.533 | 0.50 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.50, |
| trend_following_v1 | BTC-USD | 4h | 4000 | 4 | -0.006 | 0.561 | 0.62 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.62, |
| trend_following_v1 | BTC-USD | 5m | 4000 | 4 | -0.010 | 0.150 | 0.22 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.22, |
| trend_following_v1 | ETH-USD | 15m | 4000 | 4 | -0.031 | 0.366 | 0.62 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.62, |
| trend_following_v1 | ETH-USD | 1d | 900 | 2 | 0.075 | 0.980 | 0.21 | ✅ | survives the overfitting gauntlet (PBO 0.21, deflated Sharpe |
| trend_following_v1 | ETH-USD | 1h | 4000 | 4 | 0.012 | 0.504 | 0.58 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.58, |
| trend_following_v1 | ETH-USD | 30m | 4000 | 4 | -0.013 | 0.639 | 0.07 | — | survives the overfitting gauntlet (PBO 0.07, deflated Sharpe |
| trend_following_v1 | ETH-USD | 4h | 4000 | 4 | 0.010 | 0.850 | 0.97 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.97, |
| trend_following_v1 | ETH-USD | 5m | 4000 | 4 | -0.005 | 0.281 | 0.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.00, |
| trend_following_v1 | SOL-USD | 15m | 4000 | 4 | -0.018 | 0.537 | 0.51 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.51, |
| trend_following_v1 | SOL-USD | 1d | 838 | 2 | 0.019 | 0.637 | 0.92 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.92, |
| trend_following_v1 | SOL-USD | 1h | 4000 | 4 | 0.036 | 0.586 | 0.57 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.57, |
| trend_following_v1 | SOL-USD | 30m | 4000 | 4 | -0.015 | 0.702 | 0.53 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.53, |
| trend_following_v1 | SOL-USD | 4h | 4000 | 4 | 0.024 | 0.771 | 0.08 | ✅ | survives the overfitting gauntlet (PBO 0.08, deflated Sharpe |
| trend_following_v1 | SOL-USD | 5m | 4000 | 4 | -0.033 | 0.029 | 0.89 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.89, |
| trend_following_v1 | XAUUSD | 15m | 4000 | 4 | -0.028 | 0.120 | 0.94 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.94, |
| trend_following_v1 | XAUUSD | 1h | 2360 | 2 | -0.003 | 0.373 | 0.49 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.49, |
| trend_following_v1 | XAUUSD | 30m | 4000 | 4 | -0.000 | 0.168 | 0.26 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.26, |
| trend_following_v1 | XAUUSD | 4h | 590 | 2 | -0.002 | 0.462 | 0.52 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.52, |
| trend_following_v1 | XAUUSD | 5m | 4000 | 4 | -0.031 | 0.059 | 0.63 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.63, |
| trend_following_v2 | BTC-USD | 15m | 4000 | 4 | -0.012 | 0.189 | 0.65 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.65, |
| trend_following_v2 | BTC-USD | 1d | 939 | 2 | -0.075 | 0.687 | 0.61 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.61, |
| trend_following_v2 | BTC-USD | 1h | 4000 | 4 | -0.012 | 0.313 | 0.63 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.63, |
| trend_following_v2 | BTC-USD | 30m | 4000 | 4 | -0.065 | 0.539 | 0.94 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.94, |
| trend_following_v2 | BTC-USD | 4h | 4000 | 4 | -0.007 | 0.520 | 0.76 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.76, |
| trend_following_v2 | BTC-USD | 5m | 4000 | 4 | -0.047 | 0.131 | 0.85 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.85, |
| trend_following_v2 | ETH-USD | 15m | 4000 | 4 | -0.039 | 0.288 | 0.85 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.85, |
| trend_following_v2 | ETH-USD | 1d | 900 | 2 | 0.097 | 0.998 | 0.15 | ✅ | survives the overfitting gauntlet (PBO 0.15, deflated Sharpe |
| trend_following_v2 | ETH-USD | 1h | 4000 | 4 | 0.002 | 0.351 | 0.65 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.65, |
| trend_following_v2 | ETH-USD | 30m | 4000 | 4 | -0.024 | 0.315 | 0.65 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.65, |
| trend_following_v2 | ETH-USD | 4h | 4000 | 4 | 0.037 | 0.904 | 0.07 | ✅ | survives the overfitting gauntlet (PBO 0.07, deflated Sharpe |
| trend_following_v2 | ETH-USD | 5m | 4000 | 4 | -0.018 | 0.329 | 0.61 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.61, |
| trend_following_v2 | SOL-USD | 15m | 4000 | 4 | -0.025 | 0.401 | 0.33 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.33, |
| trend_following_v2 | SOL-USD | 1d | 838 | 2 | 0.057 | 0.765 | 0.14 | ✅ | survives the overfitting gauntlet (PBO 0.14, deflated Sharpe |
| trend_following_v2 | SOL-USD | 1h | 4000 | 4 | 0.040 | 0.611 | 0.69 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.69, |
| trend_following_v2 | SOL-USD | 30m | 4000 | 4 | 0.010 | 0.735 | 0.82 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.82, |
| trend_following_v2 | SOL-USD | 4h | 4000 | 4 | 0.018 | 0.668 | 0.13 | ✅ | survives the overfitting gauntlet (PBO 0.13, deflated Sharpe |
| trend_following_v2 | SOL-USD | 5m | 4000 | 4 | -0.041 | 0.051 | 0.55 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.55, |
| trend_following_v2 | XAUUSD | 15m | 4000 | 4 | -0.009 | 0.300 | 0.54 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.54, |
| trend_following_v2 | XAUUSD | 1h | 2360 | 2 | -0.007 | 0.289 | 0.88 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.88, |
| trend_following_v2 | XAUUSD | 30m | 4000 | 4 | -0.011 | 0.296 | 0.52 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.52, |
| trend_following_v2 | XAUUSD | 4h | 590 | 2 | -0.044 | 0.507 | 0.86 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.86, |
| trend_following_v2 | XAUUSD | 5m | 4000 | 4 | -0.048 | 0.020 | 0.47 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.47, |
| volatility_breakout_v1 | BTC-USD | 15m | 4000 | 4 | -0.031 | 0.094 | 0.75 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.75, |
| volatility_breakout_v1 | BTC-USD | 1d | 939 | 2 | -0.084 | 0.439 | 0.84 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.84, |
| volatility_breakout_v1 | BTC-USD | 1h | 4000 | 4 | 0.003 | 0.522 | 0.74 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.74, |
| volatility_breakout_v1 | BTC-USD | 30m | 4000 | 4 | -0.037 | 0.207 | 0.07 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.07, |
| volatility_breakout_v1 | BTC-USD | 4h | 4000 | 4 | -0.019 | 0.370 | 0.39 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.39, |
| volatility_breakout_v1 | BTC-USD | 5m | 4000 | 4 | -0.018 | 0.166 | 0.53 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.53, |
| volatility_breakout_v1 | ETH-USD | 15m | 4000 | 4 | -0.031 | 0.276 | 0.74 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.74, |
| volatility_breakout_v1 | ETH-USD | 1d | 900 | 2 | 0.083 | 0.937 | 0.01 | ✅ | survives the overfitting gauntlet (PBO 0.01, deflated Sharpe |
| volatility_breakout_v1 | ETH-USD | 1h | 4000 | 4 | 0.007 | 0.658 | 0.42 | ✅ | survives the overfitting gauntlet (PBO 0.42, deflated Sharpe |
| volatility_breakout_v1 | ETH-USD | 30m | 4000 | 4 | -0.034 | 0.227 | 0.82 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.82, |
| volatility_breakout_v1 | ETH-USD | 4h | 4000 | 4 | 0.034 | 0.919 | 0.18 | ✅ | survives the overfitting gauntlet (PBO 0.18, deflated Sharpe |
| volatility_breakout_v1 | ETH-USD | 5m | 4000 | 4 | -0.025 | 0.285 | 0.06 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.06, |
| volatility_breakout_v1 | SOL-USD | 15m | 4000 | 4 | -0.031 | 0.410 | 0.69 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.69, |
| volatility_breakout_v1 | SOL-USD | 1d | 838 | 2 | 0.030 | 0.810 | 0.25 | ✅ | survives the overfitting gauntlet (PBO 0.25, deflated Sharpe |
| volatility_breakout_v1 | SOL-USD | 1h | 4000 | 4 | 0.035 | 0.583 | 0.13 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.13, |
| volatility_breakout_v1 | SOL-USD | 30m | 4000 | 4 | -0.008 | 0.435 | 0.20 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.20, |
| volatility_breakout_v1 | SOL-USD | 4h | 4000 | 4 | 0.016 | 0.901 | 0.38 | ✅ | survives the overfitting gauntlet (PBO 0.38, deflated Sharpe |
| volatility_breakout_v1 | SOL-USD | 5m | 4000 | 4 | -0.038 | 0.094 | 0.66 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.66, |
| volatility_breakout_v1 | XAUUSD | 15m | 4000 | 4 | -0.006 | 0.103 | 0.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.00, |
| volatility_breakout_v1 | XAUUSD | 1h | 2360 | 2 | -0.001 | 0.165 | 0.03 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.03, |
| volatility_breakout_v1 | XAUUSD | 30m | 4000 | 4 | 0.020 | 0.240 | 0.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.00, |
| volatility_breakout_v1 | XAUUSD | 4h | 590 | 2 | 0.106 | 0.565 | 0.13 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.13, |
| volatility_breakout_v1 | XAUUSD | 5m | 4000 | 4 | -0.033 | 0.054 | 0.00 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.00, |

## Data-limited / skipped cells (no result fabricated)

| Strategy | Symbol | TF | Bars | Status |
| --- | --- | --- | --- | --- |
| htf_momentum_v1 | BTC-USD | 1d | 939 | error: ValueError: htf 1d must be coarser than the bar timeframe 1d |
| htf_momentum_v1 | BTC-USD | 1w | 135 | insufficient-data |
| ma_crossover_v1 | BTC-USD | 1w | 135 | insufficient-data |
| mean_reversion_v1 | BTC-USD | 1w | 135 | insufficient-data |
| momentum_v1 | BTC-USD | 1w | 135 | insufficient-data |
| regime_momentum_v1 | BTC-USD | 1w | 135 | insufficient-data |
| trend_following_v1 | BTC-USD | 1w | 135 | insufficient-data |
| trend_following_v2 | BTC-USD | 1w | 135 | insufficient-data |
| volatility_breakout_v1 | BTC-USD | 1w | 135 | insufficient-data |
| htf_momentum_v1 | ETH-USD | 1d | 900 | error: ValueError: htf 1d must be coarser than the bar timeframe 1d |
| htf_momentum_v1 | ETH-USD | 1w | 129 | insufficient-data |
| ma_crossover_v1 | ETH-USD | 1w | 129 | insufficient-data |
| mean_reversion_v1 | ETH-USD | 1w | 129 | insufficient-data |
| momentum_v1 | ETH-USD | 1w | 129 | insufficient-data |
| regime_momentum_v1 | ETH-USD | 1w | 129 | insufficient-data |
| trend_following_v1 | ETH-USD | 1w | 129 | insufficient-data |
| trend_following_v2 | ETH-USD | 1w | 129 | insufficient-data |
| volatility_breakout_v1 | ETH-USD | 1w | 129 | insufficient-data |
| htf_momentum_v1 | SOL-USD | 1d | 838 | error: ValueError: htf 1d must be coarser than the bar timeframe 1d |
| htf_momentum_v1 | SOL-USD | 1w | 120 | insufficient-data |
| ma_crossover_v1 | SOL-USD | 1w | 120 | insufficient-data |
| mean_reversion_v1 | SOL-USD | 1w | 120 | insufficient-data |
| momentum_v1 | SOL-USD | 1w | 120 | insufficient-data |
| regime_momentum_v1 | SOL-USD | 1w | 120 | insufficient-data |
| trend_following_v1 | SOL-USD | 1w | 120 | insufficient-data |
| trend_following_v2 | SOL-USD | 1w | 120 | insufficient-data |
| volatility_breakout_v1 | SOL-USD | 1w | 120 | insufficient-data |
| htf_momentum_v1 | XAUUSD | 1d | 99 | insufficient-data |
| ma_crossover_v1 | XAUUSD | 1d | 99 | insufficient-data |
| mean_reversion_v1 | XAUUSD | 1d | 99 | insufficient-data |
| momentum_v1 | XAUUSD | 1d | 99 | insufficient-data |
| regime_momentum_v1 | XAUUSD | 1d | 99 | insufficient-data |
| trend_following_v1 | XAUUSD | 1d | 99 | insufficient-data |
| trend_following_v2 | XAUUSD | 1d | 99 | insufficient-data |
| volatility_breakout_v1 | XAUUSD | 1d | 99 | insufficient-data |
| htf_momentum_v1 | XAUUSD | 1w | 15 | insufficient-data |
| ma_crossover_v1 | XAUUSD | 1w | 15 | insufficient-data |
| mean_reversion_v1 | XAUUSD | 1w | 15 | insufficient-data |
| momentum_v1 | XAUUSD | 1w | 15 | insufficient-data |
| regime_momentum_v1 | XAUUSD | 1w | 15 | insufficient-data |
| trend_following_v1 | XAUUSD | 1w | 15 | insufficient-data |
| trend_following_v2 | XAUUSD | 1w | 15 | insufficient-data |
| volatility_breakout_v1 | XAUUSD | 1w | 15 | insufficient-data |

_Full per-cell metrics + chosen params in `results.json`._

