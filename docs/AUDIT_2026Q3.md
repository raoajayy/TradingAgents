# TradingAgents Pro — Independent Competitive Audit, Q3 2026

**Audit date:** 9 August 2026 · **Commit:** `6a1c526` · **Method:** verify-don't-credit
— every internal claim checked against code, tests, or live artifacts I could
cite; roadmap prose was treated as marketing, not evidence. Written
independently of `docs/COMPETITIVE_TEARDOWN.md` (27 Jul, 59/100); the two are
reconciled in Appendix A only after scoring.

---

## 1. Executive summary

**Overall: 62/100.** The system is a genuinely category-leading *explainability
and safety* product wrapped around an AI thesis that its own evidence does not
support, running on infrastructure that cannot survive its own operator closing
a laptop lid.

Since the last audit the team shipped an enormous amount: a real event store,
purged CV + deflated Sharpe, portfolio VaR, conformal vol gates, TCA, FX,
point-in-time vintages, live venue integration, a TradingView terminal, and a
live testnet pilot with kill-switch drills. The score moved **59 → 62**. That
tiny delta is the most important finding in this document: *the work was real,
but almost none of it addressed the load-bearing risk.*

**The load-bearing risk:** the central architectural bet — 59 LLM agents
debating in five teams — has now been measured twice and has not beaten a
single model. The project's own ablation (`docs/evals/ablation.md`, n=20)
found a single strong model on identical evidence achieved **87.5% hit rate
vs the pipeline's 80.0%**, and **+$795 vs +$282 net P&L**, with 83% action
agreement. That result is corroborated, not contradicted, by the 2026
literature: [M3MAD-Bench](https://arxiv.org/pdf/2601.02854) finds multi-agent
debate ineffective across many domains; [Voting or Consensus?](https://arxiv.org/pdf/2502.19130)
finds same-model debate can fall *below* single-agent baselines; and one
recent study found full debate improved 249 predictions and worsened 236 —
approximately zero net benefit. Meanwhile
[StockBench](https://arxiv.org/abs/2510.02209) finds that most LLM agents,
including GPT-5 and Claude-4 class models, **fail to beat buy-and-hold** in
contamination-free evaluation.

So the honest position is: this product's differentiator is *not* that 59
agents produce better decisions — there is no evidence for that and mounting
evidence against it. Its differentiator is that **it is the only system in the
category that can prove what it decided, why, on what data, under which model
version, and what happened next.** That is a real, defensible, and currently
unmatched moat. The roadmap should stop trying to win on alpha and start
winning on *provable process* — and should run the experiment that either
rescues or retires the debate architecture.

**One-line verdict:** *World-class decision provenance; unproven decision
quality; hobbyist decision infrastructure. Fix the middle one with evidence,
not features.*

---

## 2. Competitive landscape

### 2.1 Open source

| System | Where it beats TAP | Where TAP beats it |
|---|---|---|
| [TradingAgents (upstream)](https://github.com/tauricresearch/tradingagents) | Mindshare (~95k★), simpler onboarding | Contracts, safety layer, execution, audit, calibration — TAP is the hardened fork |
| [NautilusTrader](https://nautilustrader.io/) | Rust core, nanosecond event bus, true live/backtest parity, real venue adapters at scale | Explainability, LLM reasoning, calibration record |
| [Qlib + RD-Agent](https://github.com/microsoft/qlib) | 40+ ML models, industrial factor infra, LLM-driven factor discovery at scale | Execution, safety, UI, decision audit |
| [Freqtrade](https://github.com/freqtrade/freqtrade) | Live track records, huge strategy community, hyperopt, dry-run maturity | Reasoning transparency, risk gates, institutional controls |
| [Lean / QuantConnect](https://github.com/QuantConnect/Lean) | Multi-asset backtest fidelity, corporate actions, options/futures, cloud compute | Explainability, agentic reasoning |
| [Lumibot](https://github.com/Lumiwealth/lumibot) | Broad broker coverage, options/futures/SEC/FRED, simple API | Everything on the rigor axis |
| [Agentic Trading Lab](https://github.com/Open-Finance-Lab/AgenticTrading) | Purpose-built agent benchmarking harness | Production hardening |

TAP wins **two rows outright across the whole open-source field**:
decision provenance (hash-chained audit + version stamps + export packs) and
outcome-graded calibration. It loses every data-depth, execution-latency,
backtest-fidelity, and scale row to at least one free alternative.

### 2.2 Commercial

Bloomberg Terminal is **$24–32k/user/yr**; LSEG Workspace ≈ **$22k**; FactSet
≈ **$12k**; [Koyfin](https://chartinglens.com/blog/best-koyfin-alternatives)
$39–79/mo; TrendSpider $22–79/mo. A widely-cited retail stack — Koyfin +
TradingView + a news-AI tool — reportedly covers 80–90% of typical Bloomberg
usage for **under $100/mo**
([analysis](https://nownews.dev/blog/bloomberg-terminal-alternatives-2026)).

That price umbrella is TAP's commercial opening and its ceiling: it cannot
out-data Bloomberg, but a **$49–199/mo provable-decision terminal** sits in a
gap nobody occupies. Composer/Capitalise-style "AI strategy" products compete
on automation, not provability; Glassnode/Nansen compete on data, not
reasoning; Tickeron/Kavout compete on signals with weak track-record hygiene.

### 2.3 Institutional practice

Citadel/Jane Street/HRT/Jump-class firms are not competitors — they are the
benchmark for *process*: independent risk systems, per-desk attribution,
mandatory pre-trade limits, T+0 reconciliation, immutable order audit,
segregated environments, and formal model-risk governance (SR 11-7 style).
TAP has genuinely institutional *instincts* (kill switches in three layers,
fail-closed gates, hash-chained audit, arming ceremonies) implemented at
hobbyist *scale* (one process, one laptop, one shared token).

### 2.4 Academic (last 24 months)

- [StockBench](https://arxiv.org/abs/2510.02209) — contamination-free multi-month
  evaluation; most LLM agents fail to beat buy-and-hold.
- [M3MAD-Bench](https://arxiv.org/pdf/2601.02854) — multi-agent debate is not
  reliably effective across domains/modalities.
- [Voting or Consensus?](https://arxiv.org/pdf/2502.19130) — decision protocol
  matters more than debate; same-model debate can underperform single agents.
- [Toward Reliable Evaluation of LLM-Based Financial Multi-Agent Systems](https://arxiv.org/html/2603.27539v1)
  — coordination primacy and **cost awareness**; the field under-reports
  transaction costs and compute cost per decision.
- [Mitigating Look-Ahead Bias in Financial Backtesting with LLMs](https://arxiv.org/html/2605.24564)
  — prompt-level instructions do **not** prevent leakage; TAP's anonymization
  audit (P2-03) is the right shape but is running at n=3.
- [Diverse Evidence, Better Forecasts](https://arxiv.org/pdf/2607.01661) —
  deliberation helps **under information asymmetry**. This is the one lifeline
  for TAP's architecture, and it is directly testable (see R1).

---

## 3. SWOT

**Strengths** — decision provenance; three-layer kill switch; fail-closed gate
discipline; honesty norms encoded in code and UI; contracts-first typing;
outcome-graded calibration; drill culture; a real TradingView terminal.

**Weaknesses** — the debate thesis is unevidenced; ~20 min/decision latency;
single-process/single-writer; live trading depends on a laptop staying awake;
free-tier-only data; live track record n≈1; shared operator token; no HA.

**Opportunities** — own "provable AI trading" as a category; sell the audit
trail, not the alpha; contamination-proof public benchmark (The Referee);
calibration-priced subscriptions; regulatory tailwind on AI model governance.

**Threats** — the category's core premise may be false (StockBench); upstream
TradingAgents' mindshare; a well-funded competitor bolting explainability onto
NautilusTrader in a quarter; LLM provider cost/rate volatility; the operator
being a single point of failure for both engineering and operations.

---

## 4. Feature comparison matrix

Legend: ✔✔ best-in-class · ✔ present · ~ partial · ✘ absent.
Columns: **TAP** (this system) · UP TradingAgents upstream · NT NautilusTrader ·
QL Qlib+RD-Agent · LN Lean · FQ Freqtrade · OB OpenBB · TV TradingView ·
BB Bloomberg · CP Composer · GN Glassnode/Nansen · INST institutional norm.

| # | Capability | TAP | UP | NT | QL | LN | FQ | OB | TV | BB | CP | GN | INST |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Market coverage (asset classes) | ~ | ~ | ✔✔ | ✔ | ✔✔ | ~ | ✔✔ | ✔✔ | ✔✔ | ~ | ~ | ✔✔ |
| 2 | Symbols live in product | ✘ (6) | ~ | ✔✔ | ✔✔ | ✔✔ | ✔✔ | ✔✔ | ✔✔ | ✔✔ | ✔ | ✔ | ✔✔ |
| 3 | Point-in-time data | ✔ | ✘ | ~ | ✔✔ | ✔ | ✘ | ~ | ✘ | ✔✔ | ✘ | ~ | ✔✔ |
| 4 | Tick / L2 / LOB | ✘ | ✘ | ✔✔ | ~ | ✔ | ~ | ✘ | ~ | ✔✔ | ✘ | ✘ | ✔✔ |
| 5 | Order flow / footprint / DOM | ✘ | ✘ | ✔ | ✘ | ~ | ✘ | ✘ | ~ | ✔ | ✘ | ✘ | ✔✔ |
| 6 | Options surface / flow | ~ (GVZ only) | ✘ | ✔ | ✘ | ✔✔ | ✘ | ✔ | ✔ | ✔✔ | ✘ | ✘ | ✔✔ |
| 7 | On-chain | ✔ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔ | ✘ | ~ | ✘ | ✔✔ | ✔ |
| 8 | Macro | ✔ | ~ | ✘ | ~ | ✔ | ✘ | ✔✔ | ~ | ✔✔ | ✘ | ✘ | ✔✔ |
| 9 | News + sentiment | ✔ | ✔ | ✘ | ~ | ✔ | ~ | ✔ | ✔ | ✔✔ | ✘ | ~ | ✔✔ |
| 10 | Alt data (satellite/shipping/cards) | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ~ | ✘ | ✔✔ | ✘ | ~ | ✔✔ |
| 11 | Pre-trade risk gates | ✔✔ | ✘ | ✔ | ✘ | ✔ | ~ | ✘ | ✘ | ✔ | ~ | ✘ | ✔✔ |
| 12 | Portfolio VaR / correlation caps | ✔ | ✘ | ~ | ~ | ✔ | ✘ | ✘ | ✘ | ✔✔ | ~ | ✘ | ✔✔ |
| 13 | Kill switch (multi-layer) | ✔✔ | ✘ | ~ | ✘ | ~ | ~ | ✘ | ✘ | ✔ | ✘ | ✘ | ✔✔ |
| 14 | Dead-man switch | ✔✔ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ✘ | ✔✔ |
| 15 | Reconciliation loop | ✔ | ✘ | ✔ | ✘ | ✔ | ~ | ✘ | ✘ | ✔✔ | ~ | ✘ | ✔✔ |
| 16 | Execution latency class | ~ (min) | ~ | ✔✔ (µs) | n/a | ✔ (ms) | ✔ (s) | n/a | n/a | ✔ | ✔ | n/a | ✔✔ (ns–µs) |
| 17 | Order types (TWAP/iceberg/algo) | ~ (TWAP) | ✘ | ✔✔ | ✘ | ✔ | ~ | ✘ | ✘ | ✔✔ | ✘ | ✘ | ✔✔ |
| 18 | TCA | ✔ | ✘ | ✔ | ✘ | ~ | ✘ | ✘ | ✘ | ✔✔ | ✘ | ✘ | ✔✔ |
| 19 | Broker/venue integrations | ~ (2) | ✘ | ✔✔ | ✘ | ✔✔ | ✔✔ | ~ | ✔ | ✔✔ | ✔ | ✘ | ✔✔ |
| 20 | Decision explainability | ✔✔ | ✔ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ | ~ | ✘ | ~ |
| 21 | Evidence→source attribution | ✔✔ | ~ | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ✔ | ✘ | ~ | ✔ |
| 22 | Hash-chained decision audit | ✔✔ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ✘ | ✔✔ |
| 23 | Version stamping (model/prompt/code) | ✔✔ | ✘ | ~ | ~ | ~ | ✘ | ✘ | ✘ | ~ | ✘ | ✘ | ✔✔ |
| 24 | Outcome-graded calibration | ✔✔ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ~ |
| 25 | Multi-agent LLM reasoning | ✔✔ | ✔✔ | ✘ | ~ | ✘ | ✘ | ~ | ✘ | ~ | ✘ | ✘ | ~ |
| 26 | Memory / historical analogs | ✔ | ~ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ✘ | ✔ |
| 27 | Knowledge graph | ✔ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔ | ✘ | ~ | ✔ |
| 28 | Backtest fidelity | ✔ | ✘ | ✔✔ | ✔ | ✔✔ | ✔ | ✘ | ~ | ✔ | ~ | ✘ | ✔✔ |
| 29 | Walk-forward / purged CV | ✔ | ✘ | ~ | ✔✔ | ✔ | ✔ | ✘ | ✘ | ~ | ✘ | ✘ | ✔✔ |
| 30 | Overfitting stats (DSR/PBO) | ✔✔ | ✘ | ✘ | ✔ | ~ | ✘ | ✘ | ✘ | ~ | ✘ | ✘ | ✔✔ |
| 31 | Monte Carlo | ✔ | ✘ | ~ | ✔ | ✔ | ~ | ✘ | ✘ | ✔ | ✔ | ✘ | ✔✔ |
| 32 | Portfolio optimizer | ~ | ✘ | ~ | ✔ | ✔ | ✘ | ~ | ✘ | ✔✔ | ✔ | ✘ | ✔✔ |
| 33 | RL | ~ (advisory) | ✘ | ✘ | ✔✔ | ~ | ✔ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔ |
| 34 | Conformal / uncertainty gating | ✔✔ | ✘ | ✘ | ~ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ |
| 35 | Charting depth | ✔ | ✘ | ✘ | ✘ | ~ | ~ | ~ | ✔✔ | ✔ | ✘ | ✔ | ✔ |
| 36 | Visualization of reasoning | ✔✔ | ~ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ✘ |
| 37 | Alerting | ✔ | ✘ | ~ | ✘ | ~ | ✔ | ✘ | ✔✔ | ✔✔ | ~ | ✔ | ✔✔ |
| 38 | Mobile | ✘ | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ✔✔ | ✔✔ | ✔ | ✔ | ✔ |
| 39 | Public API | ~ | ✘ | ✔ | ✔ | ✔✔ | ✔ | ✔✔ | ✔ | ✔✔ | ~ | ✔✔ | ✔✔ |
| 40 | Plugin/strategy marketplace | ~ | ✘ | ~ | ✘ | ✔✔ | ✔ | ✔ | ✔✔ | ✔ | ✔✔ | ✘ | ~ |
| 41 | Paper trading | ✔✔ | ~ | ✔✔ | ✘ | ✔✔ | ✔✔ | ✘ | ✔ | ✔ | ✔ | ✘ | ✔✔ |
| 42 | Live trading | ~ (testnet) | ✘ | ✔✔ | ✘ | ✔✔ | ✔✔ | ✘ | ~ | ✔✔ | ✔✔ | ✘ | ✔✔ |
| 43 | Multi-tenant auth/roles | ~ | ✘ | ✘ | ✘ | ✔ | ✘ | ~ | ✔✔ | ✔✔ | ✔ | ✔ | ✔✔ |
| 44 | Per-user attribution | ✘ | ✘ | ✘ | ✘ | ✔ | ✘ | ✘ | ✔ | ✔✔ | ✔ | ✔ | ✔✔ |
| 45 | Compliance surveillance | ✘ | ✘ | ✘ | ✘ | ~ | ✘ | ✘ | ✘ | ✔✔ | ✘ | ✘ | ✔✔ |
| 46 | Observability (metrics/health) | ✔ | ✘ | ✔ | ~ | ✔ | ✔ | ~ | n/a | ✔✔ | ~ | ~ | ✔✔ |
| 47 | HA / failover | ✘ | ✘ | ✔ | n/a | ✔✔ | ~ | ~ | ✔✔ | ✔✔ | ✔ | ✔ | ✔✔ |
| 48 | Horizontal scale | ✘ | ✘ | ✔✔ | ✔✔ | ✔✔ | ✔ | ✔ | ✔✔ | ✔✔ | ✔ | ✔✔ | ✔✔ |
| 49 | Disaster recovery (tested) | ✔ | ✘ | ~ | ✘ | ✔ | ~ | ✘ | ✔ | ✔✔ | ~ | ✔ | ✔✔ |
| 50 | Cost to operate | ✔ (~$40/mo + LLM) | ✔✔ | ✔✔ | ✔✔ | ~ | ✔✔ | ✔✔ | ✔ | ✘ | ~ | ~ | ✘ |

**Row count won by TAP outright (✔✔ where no free alternative matches):**
13, 14, 20, 21, 22, 23, 24, 30, 34, 36 — ten rows, all in the
*provenance / rigor / safety* cluster. **Rows lost to a free alternative:**
1, 2, 4, 5, 6, 16, 17, 19, 28, 42, 47, 48 — all in *data, execution, scale*.

---

## 5–15. Scores

Each score states the evidence and **what would move it**.

### 5. Architecture — 7.0/10
Contracts-first typing (`tradingagents/contracts/`, frozen + `extra="forbid"`),
clean protocol seams (`ingestion/base.py`, `execution/interface.py`,
`with_structured_output`), agents-as-config (ADR-0014), deterministic gates
separated from LLM argument (ADR-0018). Genuinely good bones. Held back by:
one process doing loop + dashboard + SSE + SQLite writes; `service.py` at 1,337
lines is a god-object; no queue between decision and execution.
**Moves it:** extract the trading loop into its own worker process with a
durable queue; decompose `service.py`.

### 6. AI — 5.0/10
Sophisticated orchestration (59 specs, 5 teams, debate, critic
self-consistency, reflection, judge, conformal gating) and honest abstention
taxonomy. But: **the architecture's value is unevidenced and its own ablation
is negative** (`docs/evals/ablation.md`); stability harness ran at n=20 and the
follow-up "re-measure at k≥30 on the production provider" is still open; the
memorization audit ran at n=3; no fine-tune, no RLVR, no distillation; model
routing is static (quick/deep), not difficulty-aware.
**Moves it:** run the asymmetry experiment (R1) and either prove debate earns
its cost or retire it to a critic-only design.

### 7. Quant — 5.0/10
Real rigor exists where most competitors have none: purged K-fold, deflated
Sharpe, PBO/CSCV (`analytics/validation.py`), HAR-RV + adaptive conformal
intervals (`analytics/conformal.py`), invalidation-anchored stops, capped
Kelly, portfolio VaR. But the alpha side is thin: one mined-factor loop, no
factor zoo, no cross-sectional models, no options analytics beyond GVZ, no
microstructure. Live sample ≈ 1 closed trade.
**Moves it:** 20+ symbols with cross-sectional ranking, and n≥100 graded live
outcomes.

### 8. UX — 8.0/10
Best-in-class *within the category*: TradingView terminal with AI decision
marks, evidence→chart level plotting, gate waterfall, ask-the-record, honest
`EmptyState`s, sample-size thresholds, immovable safety chrome. Gaps: no
mobile, no multi-monitor/workspace layouts, no screener expression language,
no keyboard-first order workflow (deliberate — no manual ticket), information
density below Bloomberg/Bookmap.
**Moves it:** mobile push + a real screener.

### 9. Security — 6.0/10
Good instincts: credential redaction everywhere, `_FILE` secret resolution,
prompt-injection fencing (`wrap_untrusted`), hash-chained audit, fail-closed
gates, no withdrawal-capable API scopes. Gaps (nine self-declared in
`docs/CONTROLS.md` §5): shared operator token with no per-person attribution,
**audit chain is unsigned** (truncation is undetectable), no SIEM, manual
secret rotation, in-process rate limiting, `SKIP_STAGING=1` bypass exists, no
independent pen test. Agent isolation is prompt-level, not sandbox-level.
**Moves it:** sign the audit chain (Ed25519 + anchored checkpoints) and issue
per-person tokens.

### 10. Infrastructure — 4.5/10
The weakest dimension and the one that bites weekly. Single uvicorn worker,
single SQLite writer, single region, no HA, no autoscale. **Live trading
currently depends on a MacBook staying awake** — the dead-man switch tripped
twice this week from host sleep (correctly), each time halting the campaign and
requiring a manual audited reset. Litestream replication with a *tested*
restore drill is the bright spot.
**Moves it:** move the armed loop to an always-on Linux host; queue + worker
split; multi-region read replica.

### 11. Explainability — 9.5/10
The moat. Evidence carries mandatory data refs and source attribution attached
**by code, not by the model** (ADR-0015); every run is version-stamped
(git sha, prompt hash, model ids, config hash); gate waterfall shows exactly
where a decision died; run-diff explains what changed the machine's mind;
export packs are audit-ready; the UI distinguishes accepted / blocked /
rejected / no-data honestly. Nothing in the open-source or retail commercial
field matches this. Half a point withheld only because the *audit chain is
unsigned* and calibration data is still thin.

### 12. Production readiness — 6.0/10
Strong drill culture (kill-switch drill passing on a real venue, restore
drill, staging→smoke→prod), real fail-closed behavior repeatedly validated in
anger. But this week alone produced: a silent entry halt recorded as
"accepted" (fixed in `6a1c526`), two false-positive dead-man trips from health
misclassification (fixed) and host sleep (correct but disruptive), a live
venue double-fill caught only by the conformance suite, test residue polluting
the live book twice, and a Docker port collision silently swallowing API calls.
Each was found and fixed properly — but the rate says pre-production.
**Moves it:** two weeks of unattended armed operation with zero manual
interventions.

### 13. Institutional readiness — 3.5/10
No SOC 2, no per-user attribution, no segregation of duties (the operator is
engineer, approver, and auditor), no HA, no prime-broker or FIX connectivity,
no compliance surveillance, no model-risk governance document mapped to
SR 11-7. `docs/CONTROLS.md` is an honest start, not an attestation.
**Moves it:** per-person identity + signed audit + an independent audit.

### 14. Innovation — 8.0/10
Several genuinely novel ideas, some already built: contamination-proof
public track record, calibration-gated marketplace (publishing requires graded
outcomes — the inverse of every signal marketplace), "what changed the
machine's mind" run diffs, evidence-chip→chart plotting, arming ceremonies with
TTL expiry, advisory-only RL by explicit decision. The unbuilt P5 set (The
Referee, audit-trail-as-a-service, calibration-priced pricing) is stronger than
most funded startups' roadmaps.

### 15. Overall — **62/100**
Weighted: Architecture 10%, AI 15%, Quant 15%, UX 10%, Security 10%,
Infrastructure 10%, Explainability 10%, Production 10%, Institutional 5%,
Innovation 5% → **6.2**.

---

## 16. Top 100 missing features

**Data (1–15):** L2/order book · tick data · footprint/DOM · options chain +
Greeks · options flow/unusual activity · dark-pool prints · ETF
creation/redemption flows · whale/wallet tracking · exchange netflows ·
stablecoin supply · funding-rate term structure · borrow/short interest ·
satellite/shipping/energy alt-data · corporate actions · earnings
transcripts + guidance.

**Coverage (16–25):** equities · ETFs · futures term structure · options ·
more FX crosses · 20+ crypto pairs · indices · commodities beyond gold ·
rates/credit · cross-listing/ADR mapping.

**Execution (26–38):** limit/post-only orders · iceberg · VWAP/POV algos ·
smart order routing · multi-venue best execution · maker-rebate awareness ·
partial-fill handling depth · slippage prediction model · pre-trade impact
estimate · borrow/locate checks · position netting across venues · FIX
connectivity · prime-broker integration.

**Quant (39–52):** cross-sectional ranking · factor zoo + orthogonalization ·
regime-conditional sizing · vol-target portfolio construction · risk parity ·
Black-Litterman · hierarchical risk parity · transaction-cost-aware
optimization · execution-aware backtest · options pricing/vol surface ·
term-structure models · jump detection · microstructure features (OFI,
Kyle's λ) · alpha decay monitoring.

**AI (53–68):** difficulty-aware model routing · speculative/cascade execution
· distilled small model for hot path · fine-tuned domain model (RLVR) ·
tool-calling agents (query data on demand) · graph-of-thoughts synthesis ·
hallucination detector on evidence claims · numeric-consistency verifier ·
per-agent calibration weighting · adaptive roster (mute low-value agents) ·
online learning from outcomes · counterfactual replay ("what if agent X
flipped") · adversarial red-team agent · cost-aware planner · context
compression · MCP tool surface.

**Risk (69–76):** stress testing / scenario library · liquidity-adjusted VaR ·
concentration limits by sector/factor · intraday drawdown control ·
correlation regime alerts · tail-hedge suggestions · margin/liquidation
simulation · counterparty exposure limits.

**Product (77–88):** mobile app + push · multi-monitor workspaces · screener
expression language · saved layouts per asset class · portfolio-level
what-if · trade-idea sharing · team accounts · white-label kit · public API
keys with quotas · webhook builder UI · CSV/Excel add-in · Slack/Discord bot.

**Ops/compliance (89–100):** per-user attribution · signed audit chain ·
SIEM export · automated secret rotation · SOC 2 controls evidence · model-risk
governance doc · surveillance (wash/spoofing patterns) · pre-trade compliance
rules engine · HA/failover · multi-region · autoscaling workers · incident
runbook automation.

---

## 17. Top 50 critical weaknesses

*(Ranked; each is a defect or structural risk, not a missing feature.)*

1. Debate architecture has negative internal evidence and no positive external
   evidence — the core bet is unvalidated.
2. Live track record n≈1 — the calibration claim has almost no data.
3. Armed live trading depends on a laptop staying awake.
4. Single process = single point of failure for loop, API, SSE, and DB writes.
5. Audit chain unsigned — truncation undetectable.
6. Shared operator token; no per-person attribution.
7. ~20 min per decision (claude-cli); rate-limit storms on free tiers.
8. No HA, no failover, single region.
9. Operator is engineer + approver + auditor (no segregation of duties).
10. `service.py` god-object (1,337 lines) concentrates risk.
11. Free-tier-only data caps the achievable edge.
12. No L2/microstructure → no execution alpha, no informed sizing.
13. Backtest lacks execution-aware fills (no queue position, no impact).
14. Memorization audit at n=3 is not an audit.
15. Stability harness follow-up (k≥30) never run.
16. Model provider swap changes behavior with no eval gate in CI.
17. LLM cost per decision unmeasured against value produced.
18. Conformance residue polluted the live book twice (now fixed).
19. Venue coid reuse double-filled live before the session guard.
20. Dead-man semantics required two fixes in one week.
21. Silent entry halts recorded as "accepted" (fixed this audit).
22. No queue between decision and execution — a crash mid-flight is ambiguous.
23. SQLite single-writer blocks horizontal scale by construction.
24. No canary/blue-green for the trading loop itself.
25. Prompt injection defense is fencing only, not sandboxing.
26. No numeric verification of LLM-quoted figures against source data.
27. Agent roster is static; low-value agents still consume budget.
28. No per-agent calibration weighting in consensus.
29. RL is advisory-only and effectively unused.
30. Knowledge graph is hand-seeded, not learned.
31. Memory retrieval quality spot-checked at 5/10.
32. No cross-sectional view — decisions are per-symbol silos.
33. Portfolio optimizer absent; sizing is per-trade.
34. No stress/scenario testing.
35. No liquidity-adjusted risk.
36. Funding/borrow modeled partially.
37. TWAP exists but sliced-vs-single TCA comparison never run.
38. No mobile → operator blind when away from desk.
39. Alert fatigue risk: reconciliation drift alarms fired repeatedly.
40. No automated recovery from benign drift (manual close required).
41. Public API rate limiting is in-process (defeated by multi-instance).
42. Secrets rotation manual.
43. `SKIP_STAGING=1` deploy bypass exists.
44. No independent security review or pen test.
45. Dependency supply chain: `npm i -g @anthropic-ai/claude-code` in the image.
46. Litestream v0.3 pinned; restore path depends on one tool version.
47. Evidence of restore drill is a script comment, not an artifact.
48. Frontend bundle 586 KB gz 181 KB — heavy for a trading UI.
49. Playwright/vitest cover happy paths; few adversarial UI states.
50. Documentation debt: two conflicting P-numbering schemes, one superseded
    OpenBB recommendation (ADR-0032 contradicts `docs/OPENBB_EVALUATION.md`).

---

## 18. Top 50 strengths

**Provenance (1–10):** hash-chained audit · version stamps on every run ·
code-attached evidence attribution · export packs · run diffs · gate waterfall
· node timings · snapshot retention · reproducible prompts (hashed) ·
decision→fill linkage with honest "inferred" labels.

**Safety (11–22):** three-layer kill switch · dead-man on execution health ·
arming tiers with TTL expiry · typed confirmation ceremonies · canary
min-size clamp · atomic entry+venue-resting stop · fail-closed everything ·
reconciliation loop · emergency flatten · circuit breaker · notional/rate/
spread caps · live-config refuses missing keys.

**Honesty (23–32):** `missing_feeds` disclosure · abstention taxonomy
separating no-data from model-broken · four honest empty states ·
sample-size thresholds · no fabricated data anywhere · degraded-feed banner ·
stale-price dimming · "EOD data" badges · inferred-link labeling ·
documented out-of-scope list.

**Engineering (33–42):** contracts-first typing · protocol seams · agents as
config · deterministic gates vs LLM argument · hermetic test suite ·
fake+real venue conformance parametrization · atomic writes + fsync appends ·
purged CV/DSR/PBO · conformal vol gating · ADR discipline.

**Product (43–50):** TradingView terminal with AI marks · evidence→chart
plotting · ask-the-record Q&A · calibration leaderboard · outcome grading ·
public track record scaffolding · marketplace publish gate · immovable
safety chrome.

---

## 19. Top 50 opportunities

*(Condensed to the 12 that matter; the remainder are permutations.)*
1. Own "provable AI trading" as a category name.
2. Sell the audit trail to funds that must explain AI decisions to LPs.
3. The Referee: a hosted contamination-proof benchmark others submit to.
4. Calibration-priced subscription (pay less when the model is wrong).
5. Regulatory tailwind: AI model governance mandates favor provenance.
6. White-label the safety layer to crypto funds without one.
7. License the execution-safety module standalone.
8. Publish the ablation honestly → credibility in a field of overclaiming.
9. Partner with an OSS backtester (Nautilus) rather than rebuild.
10. Prop-firm channel: risk-gate + audit is exactly their compliance need.
11. Academic collaboration on the asymmetry hypothesis.
12. Data-vendor arbitrage: one paid lane (options flow) leapfrogs many rows.

---

## 20. Top 50 risks

**Existential (1–6):** the category premise is false (StockBench) · debate
adds cost without alpha · a single bad live loss destroys the record · the
operator is a bus-factor of one · LLM provider policy/pricing shock · a
competitor bolts explainability onto Nautilus.
**Operational (7–20):** host sleep halts trading · silent halts (now fixed) ·
venue API changes · coid semantics differ per venue · rate limits stall
decisions · SQLite corruption · litestream restore failure · Docker port
collisions · test residue on live books · stale SW bundles · secret leakage
via logs · dependency compromise · unsigned audit repudiation · drift alarm
fatigue.
**Market/product (21–34):** free-tier data ceiling · TradingView license
terms · mirrored library drift · thin live sample invites overfitting to
n≈1 · marketplace liability · public track record legal exposure ·
mobile absence loses users · pricing below cost of LLM calls · churn from
20-min decisions · onboarding complexity · no team features · competitor
mindshare (upstream 95k★) · regulatory classification as advice · tax/report
obligations.
**Technical debt (35–50):** god-object service · dual P-numbering ·
contradictory docs · uncommitted WIP in tree · four pre-existing conformance
failures at HEAD · bundle size · prompt drift without eval gate · roster
sprawl · advisory RL rot · knowledge graph staleness · memory index quality ·
frontend test depth · e2e brittleness on iframe internals · single-region ·
manual ceremonies · doc/code divergence.

---

## 21. Roadmap

### Phase 1 — Quick wins (2 weeks)
| # | Item | Value | Complexity | Effort | ROI |
|---|---|---|---|---|---|
| 1 | Move the armed loop to an always-on Linux host | Ends the #1 operational failure | Low | 1–2 d | **Very high** |
| 2 | Sign the audit chain (Ed25519 + periodic anchor) | Makes the moat legally meaningful | Low | 2–3 d | **Very high** |
| 3 | Per-person API tokens + attribution | Unblocks every compliance conversation | Low | 2 d | High |
| 4 | Auto-heal benign reconciliation drift | Stops manual-close toil, keeps the gate | Med | 2 d | High |
| 5 | Cost-per-decision metric + budget alert | Makes the AI thesis measurable in $ | Low | 1 d | High |
| 6 | Run the k≥30 stability + n≥100 ablation re-measure | Decides the architecture's future | Low (compute) | 3 d | **Critical** |

### Phase 2 — Professional (2 months)
Queue + worker split (decision ≠ execution) · difficulty-aware model routing
with a distilled fast path (target <2 min/decision) · 20+ symbols with
cross-sectional ranking · one paid data lane (options flow **or** L2) ·
mobile push notifications · screener expression language · execution-aware
backtest fills · sliced-vs-single TCA study.

### Phase 3 — Institutional (6 months)
HA (active-passive with leader election) · Postgres migration behind the
existing store protocol · SOC 2 Type I evidence + independent pen test ·
model-risk governance doc mapped to SR 11-7 · compliance surveillance rules ·
prime-broker/FIX lane · per-desk entitlements · signed export packs for LPs.

### Phase 4 — World-class (12 months)
The Referee (hosted contamination-proof benchmark) · RLVR fine-tune on the
graded corpus (needs n≥200) · portfolio optimizer with TC-aware construction ·
options analytics (surface, flow, Greeks) · multi-venue smart routing ·
white-label/on-prem kit · public API with quotas and marketplace.

### Phase 5 — 10× bets nobody offers
1. **Audit-trail-as-a-service** — sell provenance infrastructure to *other*
   AI trading products; become the Stripe of decision provability.
2. **Calibration-priced subscription** — price falls when the model is
   miscalibrated; an economic promise no competitor dares copy.
3. **The Referee** — own the benchmark the category is judged by.
4. **Adversarial market twin** — an agent whose job is to make your strategy
   look good, then prove it can't.
5. **Regulatory export mode** — one click produces a regulator-ready dossier
   for every automated decision.

---

## 22. Ordered engineering backlog (top 20)

1. Always-on Linux host for the armed loop
2. Ed25519-signed audit chain + anchoring
3. Stability k≥30 + ablation n≥100 re-measure (**gates #4**)
4. Architecture decision: keep / prune / retire debate based on #3
5. Per-person tokens + attribution
6. Cost-per-decision + LLM budget guardrail
7. Benign-drift auto-heal
8. Decision/execution queue split
9. `service.py` decomposition
10. Difficulty-aware routing + distilled fast path
11. Postgres behind the store protocol
12. HA leader election for the loop
13. 20-symbol expansion + cross-sectional ranker
14. One paid data lane
15. Execution-aware backtest fills
16. Mobile push
17. Screener language
18. Numeric-consistency verifier on evidence
19. Per-agent calibration weighting
20. Adaptive roster (mute low-value agents)

## 23. Research backlog

**R1 (highest value): the asymmetry experiment.** [Diverse Evidence, Better
Forecasts](https://arxiv.org/pdf/2607.01661) suggests deliberation helps *under
information asymmetry* — exactly the condition TAP's team structure creates
(each team sees a different snapshot slice). Test: pipeline vs single model
where the single model receives (a) the union of all evidence, and (b) only one
team's slice. If debate only wins in (b), the architecture's value is
information routing, not debate — and can be replaced by a cheap map-reduce.
**R2:** per-agent calibration → learned consensus weights.
**R3:** distillation of the full pipeline into a small model; measure agreement
and cost. **R4:** contamination-controlled walk-forward on post-training-cutoff
data only. **R5:** counterfactual replay for causal attribution of agent
influence. **R6:** conformal calibration of *action* confidence, not just vol.
**R7:** cost-aware planner (spend deep-model calls only where they change the
verdict).

## 24. Architecture diagrams

See [`docs/analysis/ARCHITECTURE_DIAGRAMS.md`](analysis/ARCHITECTURE_DIAGRAMS.md)
— system, five data-flow, and component diagrams with per-module
responsibilities, all validated.

## 25. Future research directions
Financial time-series foundation models as evidence providers (not
predictors) · causal inference over decision logs · mechanistic
interpretability of trading LLMs · market-impact-aware RL in simulation ·
multi-agent systems under adversarial information · verifiable computation
for audit trails.

## 26. Technologies to monitor (5 years)
Small distilled reasoning models (edge/cheap hot path) · MCP as the standard
tool surface · verifiable/attested compute for audit · conformal prediction in
production risk · DuckDB/Arrow for analytics on the event store · Rust cores
for latency (Nautilus pattern) · zero-knowledge proofs of track record ·
regulation of automated advice · exchange-native co-located crypto execution ·
open-weight frontier models closing the reasoning gap.

---

## Moat analysis

**Easy to copy (weeks):** the agent roster, the prompts, the dashboard look,
TradingView integration, the free data lanes.
**Hard to copy (quarters):** the safety layer as a *system* (three-layer kill
switch + arming ceremonies + drills + fail-closed gates + reconciliation), the
honesty discipline encoded in code and UI, the contracts spine.
**Nearly impossible to copy (years, and only by starting now):** an
**aged, hash-chained, version-stamped, outcome-graded decision record**. Every
day the system runs, this asset appreciates and cannot be back-filled — a
competitor starting today is two years behind on the only axis that can't be
bought.
**Network effects:** weak today. They appear only with The Referee (others
submit to your benchmark) and the marketplace (publishers accumulate graded
records that live in your ledger).
**Defensibility action:** sign the chain (makes the record legally
citable), publish the track record continuously (makes it externally
verifiable), and open-source the *harness* while keeping the record — the same
play Numerai used.

## Product strategy

| Segment | Positioning | Price | Notes |
|---|---|---|---|
| Retail | "See why the AI traded" | Free tier / $29 | Loss leader; feeds the record |
| Serious retail / prop | Provable decisions + risk gates | $99–199/mo | Prop firms need the audit for their own compliance |
| Crypto funds | Safety layer + audit as infrastructure | $1–5k/mo | Sell the module, not the alpha |
| Family offices | LP-ready decision dossiers | $2–10k/mo | Explainability is the product |
| Banks/institutions | Model-risk governance evidence | Enterprise | Requires SOC 2 + HA first |

**Open-source strategy:** keep the framework open (mindshare vs upstream's
95k★), keep the *record* and the hosted Referee proprietary. **API strategy:**
public read-only track record free (credibility), write/execute paid.
**Do not chase:** HFT latency, options market-making, DRL alpha, TSFM return
prediction — all are lost rows against better-capitalized specialists.

---

## Ranked top-25 highest-ROI improvements

| # | Improvement | Impact | Effort | Risk | Measurable outcome |
|---|---|---|---|---|---|
| 1 | Re-measure stability k≥30 + ablation n≥100 | **High** | Low | Low | A defensible yes/no on the core architecture |
| 2 | Always-on Linux host for armed trading | High | Low | Low | Zero sleep-induced halts |
| 3 | Sign the audit chain | High | Low | Low | Tamper-evident record; enables enterprise sales |
| 4 | Act on #1 (prune or keep debate) | **High** | Med | Med | −50–80% cost/decision if pruned |
| 5 | Cost-per-decision metric | High | Low | Low | $/decision visible; budget alerts |
| 6 | Per-person tokens | High | Low | Low | Attribution in every audit line |
| 7 | Decision/execution queue split | High | Med | Med | Crash-safe execution; independent scaling |
| 8 | Difficulty-aware routing + fast path | High | Med | Med | <2 min median decision |
| 9 | Benign-drift auto-heal | Med | Low | Low | No manual closes; fewer false alarms |
| 10 | Grow live record to n≥30 graded | **High** | Low (time) | Med | Calibration claim becomes real |
| 11 | 20-symbol + cross-sectional ranking | High | Med | Med | More decisions/day → faster record growth |
| 12 | Execution-aware backtest fills | Med | Med | Low | Backtest→live gap quantified |
| 13 | One paid data lane | High | Low ($) | Low | Wins 3–5 matrix rows |
| 14 | Postgres behind store protocol | Med | Med | Med | Removes single-writer ceiling |
| 15 | HA leader election | High | High | Med | Survives instance loss |
| 16 | Numeric-consistency verifier | Med | Low | Low | Hallucinated figures caught pre-gate |
| 17 | Per-agent calibration weights | Med | Med | Med | Consensus quality ↑ measurably |
| 18 | Adaptive roster | Med | Med | Low | Cost ↓ without accuracy loss |
| 19 | Mobile push | Med | Med | Low | Operator not blind off-desk |
| 20 | SOC 2 Type I | Med | High | Low | Unblocks institutional pipeline |
| 21 | Independent pen test | Med | Low ($) | Low | Security score credible |
| 22 | `service.py` decomposition | Med | Med | Med | Change risk ↓ |
| 23 | The Referee MVP | **High** | High | High | Category ownership |
| 24 | Screener language | Med | Med | Low | Retail retention |
| 25 | Publish the ablation openly | Med | Low | Low | Credibility asymmetry vs overclaiming field |

## Unknown unknowns (flagged, not resolved)

1. Whether *any* LLM architecture beats buy-and-hold out-of-sample at horizon —
   StockBench says mostly no; TAP has no contamination-free evidence either way.
2. Whether the debate's value is information routing rather than deliberation
   (R1 is designed to find out).
3. Whether the live venue's semantics hold under stress (partial fills,
   liquidation cascades) — never tested.
4. Whether calibration on n≈1–30 generalizes at all.
5. Whether the audit trail has commercial demand or is engineer-romance.
6. Whether TradingView licensing survives commercialization.
7. LLM provider behavioral drift between pinned snapshots.
8. Real slippage at size — everything so far is dust-size.

---

## Appendix A — reconciliation with `COMPETITIVE_TEARDOWN.md` (27 Jul, 59/100)

**Closed since V1:** JSONL→SQLite event store · semantic embeddings ·
purged CV + DSR · portfolio VaR + correlation caps · TCA · funding costs ·
staging environment · FX majors · live venue integration · PIT vintages ·
conformal gates · version stamping · export packs.

**Still open, both audits agree:** L2/order flow · options flow · institutional
data · HA/scale · per-user attribution · SOC 2 · mobile · live track record
depth.

**Where the two audits disagree:**
- V1 scored **AI 5** on "no fine-tune, thin evals". I also score **5**, but for
  a *different and more serious* reason: the evals have since run and came back
  **negative for the architecture**. V1 treated the multi-agent design as an
  asset awaiting validation; I treat it as a liability awaiting justification.
- V1 scored **Infrastructure 5**; I score **4.5** — the live pilot exposed that
  the deployment story for *armed* trading is a laptop, which V1 could not
  have known.
- V1 scored **Production Readiness 6** pre-pilot; I keep **6** despite far more
  hardening, because the pilot surfaced six distinct live defects in one week.
- V1's headline was "mid-tier quant core on hobbyist infrastructure." I'd
  amend: the quant core is now respectable *in method*; what's mid-tier is the
  **evidence**, and that is fixable with time rather than code.

**Verdict on the 75 target V1 set:** not met (62). It is reachable within one
quarter, but only via items 1–10 of the ROI table — not via more features.
