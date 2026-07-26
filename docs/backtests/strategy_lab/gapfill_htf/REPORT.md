# Strategy Lab — Findings (Phases 1-2)

_Generated 2026-07-26T14:01:04.189200+00:00 · objective `sharpe` · walk-forward OOS headline + DSR/PBO guard bar (pass = DSR≥0.6 & PBO≤0.5 & OOS-Sharpe>0)._

**0 / 3 scored cells pass the guard bar** (3 cells were data-limited and not scored).

## Leaderboard — guard-passing cells (by OOS Sharpe)

_No cell passed the guard bar on the available data._

## All scored cells

| Strategy | Symbol | TF | Bars | Wins | OOS Sharpe | DSR | PBO | Guard | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| htf_momentum_v1 | BTC-USD | 4h | 4000 | 4 | 0.005 | 0.567 | 0.17 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.17, |
| htf_momentum_v1 | ETH-USD | 4h | 4000 | 4 | 0.029 | 0.655 | 0.54 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.54, |
| htf_momentum_v1 | SOL-USD | 4h | 4000 | 4 | -0.022 | 0.447 | 0.40 | — | no evidence of out-of-sample edge — do not deploy (PBO 0.40, |

## Data-limited / skipped cells (no result fabricated)

| Strategy | Symbol | TF | Bars | Status |
| --- | --- | --- | --- | --- |
| htf_momentum_v1 | BTC-USD | 1d | 939 | error: ValueError: htf 1d must be coarser than the bar timeframe 1d |
| htf_momentum_v1 | ETH-USD | 1d | 900 | error: ValueError: htf 1d must be coarser than the bar timeframe 1d |
| htf_momentum_v1 | SOL-USD | 1d | 838 | error: ValueError: htf 1d must be coarser than the bar timeframe 1d |

_Full per-cell metrics + chosen params in `results.json`._

