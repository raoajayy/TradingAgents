# Strategy Lab — Findings (Phases 1-2)

_Generated 2026-07-26T09:26:05.607586+00:00 · objective `sharpe` · walk-forward OOS headline + DSR/PBO guard bar (pass = DSR≥0.6 & PBO≤0.5 & OOS-Sharpe>0)._

**1 / 1 scored cells pass the guard bar** (0 cells were data-limited and not scored).

## Leaderboard — guard-passing cells (by OOS Sharpe)

| Strategy | Symbol | TF | OOS Sharpe | DSR | PBO | Trials | Stable params |
| --- | --- | --- | --- | --- | --- | --- | --- |
| htf_momentum_v2 | ETH-USD | 4h | 0.031 | 0.847 | 0.23 | 9 | `{"roc_period": 10, "roc_threshold": 4.0}` |

## All scored cells

| Strategy | Symbol | TF | Bars | Wins | OOS Sharpe | DSR | PBO | Guard | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| htf_momentum_v2 | ETH-USD | 4h | 4000 | 4 | 0.031 | 0.847 | 0.23 | ✅ | survives the overfitting gauntlet (PBO 0.23, deflated Sharpe |

_Full per-cell metrics + chosen params in `results.json`._

