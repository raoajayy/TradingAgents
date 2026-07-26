# Strategy Lab — Findings (Phases 1-2)

_Generated 2026-07-26T13:40:23.998323+00:00 · objective `sharpe` · walk-forward OOS headline + DSR/PBO guard bar (pass = DSR≥0.6 & PBO≤0.5 & OOS-Sharpe>0)._

**0 / 6 scored cells pass the guard bar** (0 cells were data-limited and not scored).

## Leaderboard — guard-passing cells (by OOS Sharpe)

_No cell passed the guard bar on the available data._

## All scored cells

| Strategy | Symbol | TF | Bars | Wins | OOS Sharpe | DSR | PBO | Guard | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| rules_v1 | BTC-USD | 1d | 939 | 2 | -0.035 | 0.413 | 0.96 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.96, |
| rules_v1 | BTC-USD | 4h | 1200 | 2 | -0.003 | 0.785 | 0.13 | — | survives the overfitting gauntlet (PBO 0.13, deflated Sharpe |
| rules_v1 | ETH-USD | 1d | 900 | 2 | -0.041 | 0.513 | 0.17 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.17, |
| rules_v1 | ETH-USD | 4h | 1200 | 2 | 0.014 | 0.499 | 0.83 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.83, |
| rules_v1 | SOL-USD | 1d | 838 | 2 | 0.045 | 0.400 | 0.93 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.93, |
| rules_v1 | SOL-USD | 4h | 1200 | 2 | 0.030 | 0.599 | 0.97 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.97, |

_Full per-cell metrics + chosen params in `results.json`._

