# Roadmap 2026 H2 — the A-epoch

Derived from [`docs/AUDIT_2026Q3.md`](AUDIT_2026Q3.md) v2 (10 Aug 2026,
61/100). Supersedes the P-epoch scope of
[`docs/ROADMAP_TASKS.md`](ROADMAP_TASKS.md) for new work; P-epoch IDs stay
valid for history. IDs are stable — reference them in commits
(`feat(a1-02): …`). Each task: What / Files / AC / Effort / Deps / Value /
Moat. Gate rules are written **before** results, per
[`docs/EVAL_PROTOCOL.md`](EVAL_PROTOCOL.md).

The audit's one-line verdict governs everything here: *world-class decision
provenance; unproven decision quality; hobbyist decision infrastructure.*
The roadmap therefore has one gate (A0: decide the architecture with
evidence), one theme for A1 (make the infrastructure survivable), and one
theme for A2+ (make the provenance moat sellable).

---

## §1 Design changes (DC-1 … DC-10)

Architecture deltas required by the audit. Each cites its motivating
finding. Everything else in this roadmap is ordinary feature work.

### DC-1 — LLM provider redundancy + failover
**Finding:** on 10 Aug every configured provider was simultaneously
unavailable (claude-cli session limit; DeepSeek 402; OpenAI, Google, xAI
unfunded/unauthorized) — audit §10; the decision layer has a five-way
single point of failure.
**Before:** `pro/models.py::bundle_from_config` builds quick/deep from ONE
provider; any provider failure is an abstention storm.
**After:** `ModelRouting` gains `fallbacks: list[ProviderModelPair]`
(pinned ids, same discipline as primaries). `ModelBundle` wraps calls: on
`LLM_REFUSED`-class errors (auth/billing/session-limit — the taxonomy in
`pro/agents/base.py` already names them) it fails over in order, emits a
`critical` alert (`provider_failover`), and stamps the *actual* model id
into the run's version stamp (provenance must record what really answered).
The `models` health check (`pro/health.py::_add_model_health`) reports the
active provider. **Stays:** the structured-output seam; pinned-model
enforcement applies to fallbacks too.
**Eval-harness part:** `pro/evals/stability.py` / `harness.py` /
`ablation.py` abort after N consecutive fatal refusals (default 3) with a
loud nonzero exit — a fully-refused run must never be scored (the 10 Aug
near-miss: 776 refusals would have read as a 0% flip rate).

### DC-2 — Confidence-gated debate (research item R1b)
**Finding:** full debate ≈ zero net benefit while *selective* debate on
low-confidence cases wins ([arXiv:2606.16047](https://arxiv.org/abs/2606.16047));
TAP debates every run at ~80 CLI calls each.
**Before:** `pipeline/graph.py` wires `join → technical_bull → … → sentiment`
unconditionally.
**After:** a conditional edge after `join`: when the evidence-consensus
confidence (`pipeline/votes.py::confidence_weighted_consensus` — already
computed) ≥ `ProConfig.debate_confidence_gate`, skip both debate pairs and
go straight to `sentiment`; the recorder writes
`debate_skipped:confidence=<x>` into the node sequence so provenance shows
*why* there was no debate. Deterministic-gate discipline: the threshold is
config, the comparison is code, the LLM has no say. **Ships only per the
A0-04 gate outcome.**

### DC-3 — Heterogeneous team models (research item R1c)
**Finding:** model heterogeneity is the one consistent fix for debate
([arXiv:2502.08788](https://arxiv.org/abs/2502.08788)); same-model agents
share correlated errors ([arXiv:2605.00914](https://arxiv.org/pdf/2605.00914)).
TAP runs all 59 agents on one model.
**Change:** none to code — `ModelRouting` per-team overrides already exist.
This is a *default-config* change contingent on the A0 mixed-roster arm
winning: e.g. TECHNICAL/QUANT on provider A, MACRO/NEWS on provider B,
debaters deliberately cross-provider. Depends on DC-1 (multiple funded
providers is the same prerequisite as failover).

### DC-4 — Deterministic decision boundary (critic demotion)
**Finding:** the measured instability lives in the LLM critic/gate
boundary — 30–50% approve/reject flip at k=10 (P-epoch gate verdict,
`docs/ROADMAP_TASKS.md`), 8/20 ablation rejections vs 0 for the single
model; LLM judges are systematically overconfident
([arXiv:2508.06225](https://arxiv.org/abs/2508.06225)).
**Before:** `pipeline/nodes.py` critic (deep model, self-consistency
majority) can unilaterally reject a run.
**After:** the critic's load-bearing check becomes a deterministic
**evidence-coverage gate** in `pipeline/gates.py`: for each debated side,
did the debate turns cite (via `cited_agent_ids`) the opposing team's
highest-confidence evidence items? Evidence objects already carry
`confidence`, `direction`, and ids — the check is set arithmetic, not
judgment. The LLM critic remains as an *annotator*: its `CriticReport` is
recorded, displayed, and counted in calibration, but can no longer flip
the verdict alone. **Fail-closed is preserved:** the deterministic gate
fails closed on missing/empty debate turns exactly like `risk_gate` does
on missing metrics. Rejections change label from `rejected:critic` to
`rejected:evidence_coverage` (dashboard vocabulary update in
`frontend/src/lib/pipelineStages.ts`).

### DC-5 — Decision/execution split via a durable outbox
**Finding:** no queue between decision and execution; a crash mid-flight
is ambiguous (audit weakness #22); `service.py` routes inline.
**Change:** accepted recommendations are written to a new `outbox` table
in the existing EventStore (`pro/store.py`) inside the same transaction as
the run record; the execution path consumes rows and marks them terminal.
Same process initially — the point is the **crash-safe handoff**: replaying
an unconsumed outbox row is idempotent because the OMS coid discipline +
the adapter session guard already prevent double-fills. This creates the
seam a later worker split (A3) uses without another migration. Keeps the
single-writer invariant (one store, one writer).

### DC-6 — Signed audit chain
**Finding:** the hash chain detects *modification* but not *truncation*
(audit weakness #5); unsigned records are repudiable — the moat isn't
legally citable.
**Change:** `pro/execution/audit.py` — each line gains an Ed25519
signature over `(prev_hash, payload_hash, seq, ts)`; a periodic anchor
checkpoint (every N lines + on clean shutdown) records `(seq, head_hash,
signature)` to a separate append-only file and optionally to an external
witness. Key via the existing `_FILE` secret convention
(`pro/secrets.py`); public key committed. New CLI:
`tradingagents-pro audit-verify` walks the chain and the anchors.
Signing failures fail closed (no unsigned writes on the money path).

### DC-7 — Always-on armed host
**Finding:** armed trading depends on a laptop lid (weakness #3); the
dead-man tripped twice in one week from host sleep — correct behavior,
wrong host.
**Change:** ops, not code. A provisioning doc + systemd unit
(`deploy/systemd/tradingagents-pro.service`) for a small always-on Linux
box/VPS: NTP-disciplined, `.env` mode 0600, litestream to the same bucket,
the laptop demoted to development and ceremonies. The readiness report
already warns on laptop-class hosts (`pro/preflight.py`) — after this, the
warning becomes a hard FAIL for `live` tier (stays WARN for canary).

### DC-8 — `service.py` decomposition
**Finding:** 1,337-line god object concentrates change risk (weakness
#10).
**Change:** mechanical split behind the existing public surface:
`pro/service/loop.py` (run_once/run_forever/skip logic),
`positions.py` (bar-close management, funding, rehydrate),
`tca.py` (arrival/markouts/TWAP absorption), `triggers.py` (P2-06),
with `pro/service.py` re-exporting `PaperTradingService` unchanged.
No behavior change; the 2,300-test suite pins it.

### DC-9 — Per-person identity
**Finding:** shared operator token; no per-person attribution (weakness
#6; `docs/CONTROLS.md` §5).
**Change:** the P3-05 `users`/`api_tokens` store already exists — issue
personal tokens, thread token identity into `audit.append` alongside the
existing `operator` field, and make ceremonies (`pro/cli.py`) refuse the
shared token once ≥1 personal token exists (migration grace, then hard).

### DC-10 — Postgres behind the store protocol (A3)
**Finding:** SQLite single-writer blocks horizontal scale by construction
(weakness #23) — correct today, a ceiling after A2-03 symbol expansion.
**Change:** a Postgres implementation of the EventStore protocol
(`pro/store.py` already isolates callers), `migrate` CLI extension, and a
cutover drill mirroring the litestream restore drill. Not before A3 — the
single-writer design is an *asset* until scale actually binds.

---

## §2 Phase A0 — the decision gate (this week)

> **Gate discipline:** A0-04's rule is written here, before any result
> exists. Whatever the data says, wins.

### A0-01 Fund one metered LLM provider — operator action, ~$10–20
- **What**: add credit to DeepSeek (house default) or OpenAI; verify with
  the provider smoke test. Blocks every other A0 item.
- **AC**: a structured-output smoke call returns OK on ≥1 metered provider.
- **Effort**: minutes. **Deps**: none. **Value**: unblocks the single
  highest-ROI item in the audit (ROI #1).

### A0-02 Eval-harness fail-fast (DC-1, harness part) — 0.5 d
- **What**: abort stability/ablation/golden runs after 3 consecutive
  `LLM_REFUSED`-class failures; nonzero exit; partial results discarded
  with an explicit "aborted: provider refused" marker.
- **Files**: `pro/evals/stability.py`, `harness.py`, `ablation.py`.
- **AC**: pytest — a fake bundle raising refusals aborts before k
  completes and exits nonzero; a fake-clean result is impossible by
  construction.
- **Deps**: none (ships even if A0-01 stalls).

### A0-03 The four-arm experiment — 1–2 d wall clock on a metered API
- **What**: on ≥100 historical cut points (`run_ablation_series`) and
  k≥30 stability (2 frozen cases): (1) pipeline-homogeneous (current),
  (2) pipeline-heterogeneous (DC-3 config), (3) single strong model on
  identical evidence, (4) confidence-gate replay (R1b — simulated from
  recorded runs: would the gate have skipped debate, and what would the
  outcome have been; costs almost nothing since it replays artifacts).
  All arms retro-scored (`analytics/retro.py`), cost-tracked
  (`observability.py`).
- **Files**: `pro/evals/__main__.py` (arm flags), `docs/evals/` outputs.
- **AC**: one table: hit rate, net P&L, action agreement, $/decision,
  flip rate per arm; committed regardless of outcome.
- **Deps**: A0-01, A0-02.

### A0-04 Architecture decision — 0.5 d, pre-registered rule
- **Rule (binding):**
  (a) single model wins outright (hit rate AND P&L, n≥100) → **debate is
  demoted to explanation generation**: one decision model, teams retained
  only to produce the evidence pack and narrative (the provenance moat
  survives; the cost center goes);
  (b) gated or heterogeneous debate beats both the current pipeline and
  the single model → **DC-2 + DC-3 become the default config**;
  (c) parity within noise → **cheapest arm wins** by measured $/decision.
- **AC**: an ADR recording the decision + the evidence; roadmap A2-01
  re-scoped accordingly.

---

## §3 Phase A1 — survivable infrastructure (2 weeks)

### A1-01 Always-on armed host (DC-7) — 1–2 d
**Files**: `deploy/systemd/tradingagents-pro.service` (new),
`docs/DEPLOYMENT.md` section, `pro/preflight.py` (laptop → FAIL for live
tier). **AC**: campaign loop survives 72 h unattended with zero
sleep-induced dead-man trips; readiness report passes on the new host.
**Value**: kills the #1 operational failure. **ROI #2.**

### A1-02 Signed audit chain (DC-6) — 2–3 d
**Files**: `pro/execution/audit.py`, `pro/secrets.py` usage, new
`audit-verify` CLI in `pro/cli.py`, key-gen doc. **AC**: verify CLI
detects (i) line tampering, (ii) truncation, (iii) anchor mismatch, each
under test; signing failure blocks the write (fail closed); existing
chain grandfathered behind a genesis anchor. **Moat**: the record becomes
legally citable. **ROI #3.**

### A1-03 Per-person tokens (DC-9) — 2 d
**Files**: `pro/dashboard/app.py` token issuance (exists), `pro/cli.py`
ceremony auth, `pro/execution/audit.py` identity field. **AC**: every
audit line carries a token identity; ceremonies refuse the shared token
when personal tokens exist; tests. **ROI #6.**

### A1-04 Cost-per-decision metric + budget alert — 1 d
**Files**: `pro/observability.py` (per-run rollup from existing
`llm_calls_total`/cost gauges), `pro/service.py` (stamp into run record),
dashboard status chip. **AC**: every run record carries `$cost`; alert
fires over a configured daily budget; the A0 table consumed this. **ROI #5.**

### A1-05 Benign-drift auto-heal — 2 d
**Files**: `pro/execution/router.py::reconcile` follow-up action behind
`PRO_RECONCILE_AUTOHEAL=1`: an `unknown_on_venue` position that matches
the residue pattern (no local order, dust-size, symbol we trade) is
closed reduce-only and audited (`reconcile_autoheal`); anything else
still blocks entries and pages. **AC**: pytest both paths; the entry
block from `6a1c526` remains for non-matching drift. **ROI #9.**

### A1-06 Publish the ablation + audit — 0.5 d
**What**: put `docs/evals/ablation.md` + `docs/AUDIT_2026Q3.md` (and the
A0 result when it lands) in the public repo/site. **Value**: credibility
asymmetry in a field of overclaiming (audit opportunity #8). **ROI #25.**

---

## §4 Phase A2 — professional grade (2 months)

### A2-01 Implement the A0 decision (DC-2/DC-3/DC-4) — 1–2 wk
Scope fixed by A0-04. In every branch, DC-4 (deterministic evidence-
coverage gate replacing the critic's veto) ships — it is justified by the
flip-rate measurement independently of which arm wins. **AC**: flip rate
at k=10 on frozen snapshots drops below 10% (from 30–50%); rejection
labels migrate; calibration keeps grading the critic-as-annotator.

### A2-02 Durable outbox + service decomposition (DC-5, DC-8) — 1–2 wk
**AC**: kill -9 between decision and order placement → restart consumes
the outbox row exactly once (test with fake venue); `service.py` ≤ 300
lines re-exporting the split modules; suite green unchanged.

### A2-03 Symbol expansion to 20+ with cross-sectional ranking — 2–3 wk
**Files**: `pro/dashboard/marketdata.py` registry, `pro/main.py` wiring
maps, `pro/arming.py` pairs, adapter `SYMBOL_MAP`s + conformance
fixtures (the exact ETH-USD trail in `docs/DEVELOPER_GUIDE.md` §7a), new
ranking view (`pro/dashboard/service.py`) ordering candidate symbols by
signal strength so the loop spends LLM budget on the best setups first.
**AC**: 20+ symbols decided per day within the LLM budget; ranking view
in the dashboard; venue-minimum-vs-cap table auto-checked at arming.
**Value**: the calibration record grows ~7× faster — the moat compounds
on n (ROI #10, #11).

### A2-04 One paid data lane — decision doc + 1 wk integration
Options flow **or** L2 depth (pick by $/row won in the matrix; the audit
says either wins 3–5 rows). New feed under `pro/ingestion/` with the
standard protocol + `missing_feeds` discipline; `docs/DATA_SOURCES.md`
updated with the paid-tier decision. **ROI #13.**

### A2-05 Mobile push (PWA web-push) — 1 wk
Service-worker push for the alert sinks (`pro/alerting.py` gains a
WebPushSink; VAPID keys via `_FILE`). **AC**: kill-switch/dead-man/fill
alerts reach a phone with the app closed. **ROI #19.**

### A2-06 Screener expression language — 1 wk
Reuse the safe-AST factor grammar (`pro/analytics/factors.py`) over the
registry's bar/indicator data; results as a dashboard view + alert
conditions. **AC**: expressions evaluate sandboxed (no attribute access —
the factor parser already enforces this); screener → chart deep-link.

### A2-07 Execution-aware backtest fills — 1 wk
`pro/backtest/broker.py`: spread + square-root impact model, partial-fill
probability by bar volume. **AC**: backtest→live TCA gap reported per
strategy; documented in `docs/BACKTESTING_GUIDE.md`. **ROI #12.**

### A2-08 Sliced-vs-single TCA study — 2 d + fills
Run the existing TWAP path vs single orders on live testnet fills;
`docs/evals/` writeup. Closes weakness #37.

---

## §5 Phase A3 — institutional grade (6 months)

- **A3-01 HA active-passive** — leader lease in the event store,
  dead-man-aware handover (the standby must *not* trade while the leader's
  lease lives); drill added to the runbook. **ROI #15.**
- **A3-02 Postgres migration (DC-10)** — behind the store protocol;
  cutover drill with rollback.
- **A3-03 SOC 2 Type I evidence pack + independent pen test** — the
  controls exist (`docs/CONTROLS.md`); this is evidence collection +
  external validation. **ROI #20, #21.**
- **A3-04 SR 11-7 model-risk governance doc** — the eval harness *is* the
  model validation function; write the mapping (development, validation,
  ongoing monitoring = calibration + drift alerts).
- **A3-05 Surveillance rules** — wash/self-cross/spoof-pattern detection
  over the audit chain; alerts, not blocking, initially.
- **A3-06 FIX / prime-broker exploration** — spike, not commitment;
  depends on there being institutional demand from A1-06/A4-05 signals.
- **A3-07 Signed LP export packs** — `decision_export_pack` + DC-6
  signatures = regulator/LP-ready dossiers (feeds A4-07).

---

## §6 Phase A4 — world-class (12 months) + 10× bets

- **A4-01 The Referee** — hosted contamination-proof benchmark (StockBench
  method, continuous, third parties submit agents; TAP's record is entry
  #1). Category-defining if it lands. **ROI #23.**
- **A4-02 RLVR fine-tune** — when the graded corpus ≥ 200
  (`pro/evals/corpus.py` gate), process-level reward verification per
  [Trade-R1](https://arxiv.org/pdf/2601.03948); target: a distilled
  decision model that matches the A0 winner at a fraction of the cost.
- **A4-03 TC-aware portfolio optimizer** — lift `backtest/portfolio_engine`
  + allocator into the live path with transaction-cost-aware rebalancing.
- **A4-04 Options analytics lane** — surface + flow (depends on A2-04 if
  options flow was the paid lane).
- **A4-05 Audit-trail-as-a-service** — the provenance stack (contracts,
  hash+sign chain, version stamps, export packs) as an embeddable SDK for
  *other* AI trading products.
- **A4-06 Calibration-priced subscription** — price falls when the model
  is miscalibrated; requires n large enough that calibration is stable
  (A2-03 feeds this).
- **A4-07 Regulatory export mode** — one click, one decision, one
  regulator-ready dossier (A3-07 + DC-6).

---

## §7 Explicit non-goals

Per the audit's product strategy (and the matrix rows lost beyond reach):
HFT latency · options market-making · DRL alpha · TSFM return prediction
(the literature says boosted trees still win;
[arXiv:2606.27100](https://arxiv.org/abs/2606.27100)) · out-dataing
Bloomberg. Competing there burns the budget that the provenance moat needs.

---

## §8 Traceability — every task answers an audit finding

| Task | Audit anchor |
|---|---|
| A0-01/02/03/04 | ROI #1, #4; weakness #1, #15; unknown-unknowns #1, #2 |
| A1-01 (DC-7) | ROI #2; weakness #3 |
| A1-02 (DC-6) | ROI #3; weakness #5; moat analysis |
| A1-03 (DC-9) | ROI #6; weakness #6, #9 |
| A1-04 | ROI #5; weakness #17 |
| A1-05 | ROI #9; weakness #39, #40 |
| A1-06 | ROI #25; opportunity #8 |
| A2-01 (DC-2/3/4) | ROI #4; weakness #1; P-epoch gate verdict; R1b/R1c |
| A2-02 (DC-5/8) | ROI #7, #22; weakness #10, #22 |
| A2-03 | ROI #10, #11; weakness #2, #32 |
| A2-04 | ROI #13; weakness #11, #12 |
| A2-05 | ROI #19; weakness #38 |
| A2-06 | missing features #77–88 (screener) |
| A2-07 | ROI #12; weakness #13 |
| A2-08 | weakness #37 |
| A3-01 | ROI #15; weakness #8 |
| A3-02 (DC-10) | ROI #14; weakness #23 |
| A3-03 | ROI #20, #21; weakness #44 |
| A3-04 | institutional readiness §13 |
| A3-05 | missing feature #95 |
| A3-07 | opportunity #2 |
| A4-01 | ROI #23; opportunity #3 |
| A4-02 | research backlog R3; AI score §6 |
| A4-05 | opportunity #2; 10× bet #1 |
| A4-06 | 10× bet #2 |
| DC-1 | Infrastructure §10 (v2 revision); ROI #1 enabler |

Top-10 ROI coverage check: #1→A0, #2→A1-01, #3→A1-02, #4→A0-04/A2-01,
#5→A1-04, #6→A1-03, #7→A2-02, #8→A2-01 (routing follows the A0 winner),
#9→A1-05, #10→A2-03. ✔ complete.

## ADR compatibility

- **ADR-0025 (RL advisory-only)** — untouched; A4-02 fine-tunes the
  *decision model* via RLVR, it does not give RL sizing authority.
- **Fail-closed norms** — DC-4 replaces an LLM veto with a deterministic
  gate that fails closed on missing input; DC-6 blocks writes on signing
  failure; A1-05's auto-heal only acts on the narrow residue pattern and
  keeps the entry block for everything else.
- **ADR-0014 (agents as config)** — DC-3 is pure config; the roster is
  unchanged until A0 evidence says otherwise.
- New ADRs required by this roadmap: the A0-04 decision itself, DC-4
  (critic demotion), DC-6 (signing scheme).
