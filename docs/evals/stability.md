# P1-01 — pass^k decision stability: baseline vs critic self-consistency

Same harness, same frozen fixtures, k=10, provider `claude-cli`.
Baseline `stability_20260729T061251Z.json` (single critic call, aliases
haiku/sonnet); verification `stability_20260729T124139Z.json`
(`critic_samples=3` majority — commit `e22e162` — pinned
`claude-haiku-4-5-20251001` / `claude-sonnet-5`).

## Gate outcomes (the metric the fix targets)

| | baseline | critic majority-of-3 |
|---|---|---|
| XAUUSD gate flip | **50%** (5 appr / 3 qual / 2 critic) | **20%** (8 appr / 1 qual / 1 critic) |
| BTC-USD gate flip | 30% (7 appr / 1 qual / 2 critic) | 30% (7 appr / 2 qual / 1 critic) |
| critic rejections (both cases) | 4/20 | **2/20** |
| quality-gate rejections | 4/20 | 3/20 |
| approvals | 12/20 | 15/20 |

## Reading

- **The fix does what it says on the critic**: critic rejections halved
  (4→2), and the worst case (XAUUSD, a coin-flip 50% boundary) dropped to
  20% gate flip with 8/10 approvals. At k=10 these rates carry ±~15%
  sampling noise — the direction is consistent with the majority-vote
  model, not yet proof.
- **The residual variance is where P1-01 said it was**: quality-gate
  rejections (3/20, inherited from the reflection stage's LLM-produced
  invalidation level moving the stop) are essentially unchanged — that is
  the documented follow-up, deliberately not restructured on n=20.
- **Newly visible: judge-level HOLD wobble.** With the critic no longer
  rejecting borderline XAUUSD runs, 2/10 surfaced as HOLD verdicts (judge
  variance the critic noise used to mask) — XAUUSD "action flip" reads 40%
  for that reason while the approve/reject boundary improved 50→20%.
  Same self-consistency treatment is applicable to the judge if this
  holds up at larger k.

## Follow-ups

1. Reflection invalidation-level variance → quality gate (needs a data
   study of stop-distance distributions before touching geometry).
2. Judge HOLD/BUY wobble on borderline setups — candidate for
   `judge_samples` mirroring the critic fix.
3. Re-measure at k≥30 on the production provider before claiming numbers.
