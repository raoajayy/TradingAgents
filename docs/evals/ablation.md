# P1-02 — Token-matched single-model ablation (debate vs one strong model)

**Run**: 2026-07-29 (`ablation_20260729T072223Z.json`) · provider `claude-cli`
(quick=haiku, deep=sonnet) · BTC-USD H4 · 20 historical Delta cut points ·
both arms see **byte-identical evidence** (deterministic indicators only, no
macro/news feeds → zero look-ahead) · tickets graded on the 42 bars that
actually followed (`simulate_ticket`).

Arms: **pipeline** = full multi-agent debate → judge → critic → gates.
**single_model** = one strong model (sonnet), one prompt over the same
evidence pack → verdict → the *same* deterministic risk/quality gates.

## Results

| metric | pipeline (debate) | single model |
|---|---|---|
| points | 20 | 20 |
| rejected (critic) | **8** | 0 |
| HOLD | 6 | 12 |
| resolved trades | 5 (+1 unresolved) | 8 |
| wins / losses | 4 / 1 | 7 / 1 |
| hit rate | 80.0% | **87.5%** |
| net simulated P&L | +$282.74 | **+$795.11** |
| action agreement (both decided) | 10/12 = **83%** | — |
| LLM calls per point | ~30–60 (agents+debate+judge+critic) | 1 |
| wall time per point | ~5–6 min | seconds |

## Reading

- **Where both arms decided, they mostly agree (83%)** — the debate is not
  producing a different market view than one strong model reading the same
  evidence. The two divergences were pipeline BUYs the single model sat out
  (one won +127, one lost −162; a wash).
- **The difference is the critic**: it rejected 8/20 points. On those 8 the
  single model traded 4 (three wins, one −269 loss — the series' worst) and
  held 4. Net effect of rejection on this sample: forgone +$135. The critic
  dodged the worst single loss but also three profitable trades.
- **The single model made ~2.8× the P&L at ~1/40th the tokens and ~1/50th
  the latency** on this sample.

## Caveats (read before acting)

- n=20 with 5–8 resolved trades per arm — far too small for a statistically
  significant P&L claim. The robust findings are the **83% agreement** and
  the **rejection-rate asymmetry** (8/20 vs 0/20), both structural rather
  than P&L-sampling artifacts, and both consistent with P1-01 (direction
  stable, gates noisy — `stability_20260729T061251Z.json`: 0 direction
  flips in 20 runs, 30–50% approve/reject flip on frozen fixtures).
- Claude models (haiku/sonnet), floating aliases; the production provider
  may gate differently. Evidence pack here is indicators-only — the debate
  may earn its keep on richer multi-feed evidence (macro conflicts, news).
- Single-model arm reuses the pipeline's deterministic gates; it is NOT
  "no risk control", it is "no LLM critic".

## Verdict against the roadmap gate

Per `ROADMAP_TASKS.md` §Phase-1: *"If the ablation shows single-model
parity, Phases 2–5 proceed unchanged but the pipeline gets cheaper (debate
kept for auditability only, one strong model decides)."*

**This sample shows single-model parity-or-better.** Combined with P1-01
(the debate's directional view is stable; the LLM critic/gates are the
noise source), the evidence points to:

1. Keep the debate for **auditability and explainability** (it is the
   product's trust surface — votes, counterarguments, dissent).
2. Make the **decision boundary deterministic**: the LLM critic should not
   be a coin-flip gate. Candidates: N-sample majority at the critic,
   hysteresis on the quality gate, or demote the critic to advisory.
3. Re-run this ablation at ≥100 points (and on the production provider)
   before any structural simplification — n=20 licenses direction-setting,
   not surgery.
