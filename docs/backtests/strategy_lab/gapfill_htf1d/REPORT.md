# Strategy Lab — Findings (Phases 1-2)

_Generated 2026-07-26T14:04:52.219783+00:00 · objective `sharpe` · walk-forward OOS headline + DSR/PBO guard bar (pass = DSR≥0.6 & PBO≤0.5 & OOS-Sharpe>0)._

**0 / 3 scored cells pass the guard bar** (0 cells were data-limited and not scored).

## Leaderboard — guard-passing cells (by OOS Sharpe)

_No cell passed the guard bar on the available data._

## All scored cells

| Strategy | Symbol | TF | Bars | Wins | OOS Sharpe | DSR | PBO | Guard | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| htf_momentum_v1 | BTC-USD | 1d | 939 | 2 | -0.002 | 0.209 | 0.96 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.96, |
| htf_momentum_v1 | ETH-USD | 1d | 900 | 2 | 0.049 | 0.430 | 0.33 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.33, |
| htf_momentum_v1 | SOL-USD | 1d | 838 | 2 | 0.011 | 0.163 | 0.82 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.82, |

_Full per-cell metrics + chosen params in `results.json`._

