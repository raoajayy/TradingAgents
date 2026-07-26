# Strategy Lab — Findings (Phases 1-2)

_Generated 2026-07-26T15:42:13.332070+00:00 · objective `sharpe` · walk-forward OOS headline + DSR/PBO guard bar (pass = DSR≥0.6 & PBO≤0.5 & OOS-Sharpe>0)._

**2 / 6 scored cells pass the guard bar** (0 cells were data-limited and not scored).

## Leaderboard — guard-passing cells (by OOS Sharpe)

| Strategy | Symbol | TF | OOS Sharpe | DSR | PBO | Trials | Stable params |
| --- | --- | --- | --- | --- | --- | --- | --- |
| momentum_v2 | ETH-USD | 1d | 0.053 | 0.610 | 0.30 | 9 | `{"entry_sigma": 0.5, "roc_period": 10}` |
| momentum_v2 | ETH-USD | 4h | 0.030 | 0.787 | 0.45 | 9 | `{"entry_sigma": 0.5, "roc_period": 10}` |

## All scored cells

| Strategy | Symbol | TF | Bars | Wins | OOS Sharpe | DSR | PBO | Guard | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| momentum_v2 | BTC-USD | 1d | 939 | 2 | -0.115 | 0.588 | 0.84 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.84, |
| momentum_v2 | BTC-USD | 4h | 4000 | 4 | 0.004 | 0.688 | 0.59 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.59, |
| momentum_v2 | ETH-USD | 1d | 900 | 2 | 0.053 | 0.610 | 0.30 | ✅ | survives the overfitting gauntlet (PBO 0.30, deflated Sharpe |
| momentum_v2 | ETH-USD | 4h | 4000 | 4 | 0.030 | 0.787 | 0.45 | ✅ | survives the overfitting gauntlet (PBO 0.45, deflated Sharpe |
| momentum_v2 | SOL-USD | 1d | 838 | 2 | 0.049 | 0.295 | 0.81 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.81, |
| momentum_v2 | SOL-USD | 4h | 4000 | 4 | 0.017 | 0.501 | 0.65 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.65, |

_Full per-cell metrics + chosen params in `results.json`._

