# Pro Pipeline Evaluation Protocol (pre-registered)

Status: v1, registered 2026-07-30 (P2-03). Changes to this protocol must
land as a new dated version *before* the runs they govern; results may not
retroactively edit the rules they were graded under.

## 1. What is being evaluated

The Pro decision pipeline (`tradingagents/pro/pipeline/`) end to end:
evidence agents → debate → judge → critic → deterministic risk/quality
gates → ticket. Entry point for all harnesses:
`python -m tradingagents.pro.evals` (see `tradingagents/pro/evals/__main__.py`).

## 2. Data windows — post-cutoff only

- Retro-scored evals (`--ablation`) use only market data with timestamps
  **after the training cutoff of the model under test** (provider-published
  cutoff; when unpublished, after the model's release date). A model cannot
  be graded on price paths it may have memorized.
- Snapshots are built strictly as-of the cut: the cut bar is in the
  snapshot, grading starts on the bar *after* it
  (`evals/ablation.py::run_ablation_series`). No macro/news feeds in
  retro runs → zero look-ahead leakage; both arms see byte-identical input.
- Frozen fixtures (`evals/golden.py`) are synthetic, deterministic, seeded —
  they carry no real calendar and are exempt from the cutoff rule.

## 3. Symbols

All traded symbols in the live universe are evaluated — currently XAUUSD
(gold) and BTC-USD; ETH/SOL join the roster when they trade. No
cherry-picking: a protocol run reports every symbol or states why one is
missing.

## 4. Fixed parameters

- Stability (`--stability`): k = 10 runs per frozen snapshot
  (`evals/stability.py`, fixtures `DEFAULT_CASE_NAMES`); k ≥ 30 required
  before any claim is called "measured" rather than "directional".
- Ablation (`--ablation`): ≥ 20 historical cut points, horizon 42 bars,
  H4; ≥ 100 points required before structural changes are licensed.
- Golden decision evals: full case list in `evals/golden.py` (direction,
  ambiguous, injection, intraday, gap tags); N samples per case declared
  at run time via `--samples`.
- Temperature 0.2 for comparability; reasoning models may ignore it, so
  conclusions rest on N-sample statistics, never single runs.

## 5. Cost-inclusive grading

Simulated P&L is net of costs, using the assumptions already wired into
the backtest/paper stack:

- **Funding**: realized perp funding charged per bar on open BTC/ETH/SOL
  positions (P1-03, `pro/backtest/broker.py`; series from Delta/Binance
  FUNDING_RATE history).
- **Slippage**: 3 bps per side assumed at entry/exit; realized markout is
  captured on paper fills (P1-04 TCA) and the Portfolio view tracks drift
  vs the 3 bps assumption. If realized slippage exceeds it, the higher
  figure is used in the next protocol version.
- LLM cost is reported alongside (CostTrackingLLM estimates printed by
  every harness run) so "wins" are judged tokens-in, P&L-out.

## 6. Phase-1 gate thresholds (baseline citations)

Recorded results the gates were set against:

- `docs/evals/stability.md` (`stability_20260729T124139Z.json`): action
  flips 0/20 on direction; gate flip 20–30% at k=10 after the
  critic-majority fix (was 30–50%). Gate: no regression above 30% gate
  flip on the frozen fixtures.
- `docs/evals/ablation.md` (`ablation_20260729T072223Z.json`): 83% action
  agreement pipeline vs single strong model; critic rejected 8/20 vs 0.
  Gate rule (ROADMAP_TASKS.md §Sequencing): single-model parity ⇒ debate
  is kept for auditability, decision boundary made deterministic.

## 7. Anonymization procedure (memorization audit)

Command: `python -m tradingagents.pro.evals --memorization-audit
[--samples N] [--seed S]` → writes `docs/evals/memorization_<stamp>.json`.

Each frozen snapshot is run twice with the **same model and same data**:

1. **Named**: the ordinary pipeline.
2. **Anonymized**: `evals/anonymize.py` masks, at two boundaries —
   `render_context(..., anonymize=)` / `anonymization_scope()` in
   `agents/rendering.py` (the snapshot→prompt boundary) and
   `anonymize_llm()` wrapping every model call:
   - tickers + asset-class vocabulary (gold/XAU/XAUUSD/bullion,
     bitcoin/BTC/XBT, …) → stable neutral tokens `ASSET_A`, `ASSET_B`
     (seedable, deterministic, first-registered order), including
     identifier segments (`GOLD_VOL_INDEX` → `ASSET_A_VOL_INDEX`);
   - absolute dates/datetimes → relative (`T-3d`, `T-0`, `T+2d`;
     clock times kept — intraday ordering is data);
   - news headlines/summaries pass through the same ticker/date scrub.
   **Numbers, prices, levels and indicator values are never masked** —
   the market data is the test; only identity and calendar are hidden.

## 8. Memorization pass/fail

Per symbol, compare per-sample action agreement (named vs anonymized,
`action_agreement` in the artifact):

- **Pass**: agreement ≥ 0.85 (drop ≤ 15 pp from the self-agreement a
  stable pipeline shows on the same fixture) and no systematic confidence
  inflation (> 10 pts mean confidence gap) in named mode.
- **Contamination flag**: agreement < 0.85
  (`anonymize.AGREEMENT_FLAG_THRESHOLD`) — the named run is drawing on
  the label, not the data. A flagged symbol's post-cutoff results are
  quarantined from any published claim until re-run on post-cutoff
  windows confirms the direction.

## 9. Artifacts

Every harness writes a timestamped JSON under `docs/evals/`
(`stability_*.json`, `ablation_*.json`, `memorization_*.json`) recording
provider, model ids, parameters and per-case rows; human-readable
summaries live beside them (`stability.md`, `ablation.md`). Published
claims must cite the artifact they came from.

## Recorded verdicts

- **2026-07-31 memorization audit (n=1 + n=3 confirmation, claude-cli
  haiku/sonnet)** — `memorization_20260730T142630Z.json`,
  `memorization_20260731T024933Z.json`. **No direction-level
  contamination**: across all 20 arm-runs, no approved decision ever
  flipped direction (every approval was BUY on both bullish fixtures,
  named or masked). **Gate-level naming sensitivity found**: pooling
  both audits, anonymized arms rejected 4/8 runs vs 1/8 named — masking
  shifts the approve/reject boundary toward rejection, while mean stated
  confidence is basically unchanged (XAUUSD 50.5 named vs 48.0 anon).
  Working hypothesis: an anonymizer ARTIFACT, not memorization — masked
  evidence ("ASSET_A") strips asset vocabulary the critic weighs, making
  the audit stage more skeptical — compounded by the known boundary
  nondeterminism (P1-01: 30–50% gate flip, reduced not eliminated).
  Distinguishing artifact from true naming reliance needs per-arm
  evidence-count/critic-issue comparison at larger n. Flags stand as
  recorded; direction-level verdict: CLEAN.
