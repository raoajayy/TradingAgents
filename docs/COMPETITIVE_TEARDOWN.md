# TradingAgents Pro — World-Class Competitive Teardown

*An adversarial 16-phase analysis by a simulated elite panel (quant PM, AI systems architect, HFT engineer, multi-agent researcher, TradingView/Bloomberg product, exchange infra, risk, behavioral finance, OSINT, VC, security, cloud, UX research). Mission: attack every weakness, compare against every serious system, and produce the roadmap to category leadership. Evidence over opinion; every external claim cited; every internal claim traces to a file path or a live production observation from the July 2026 hands-on review sessions. Date: 27 July 2026.*

---

## 1. Executive Summary

TradingAgents Pro is a single-operator, explainable multi-agent LLM trading terminal (gold + 3 crypto pairs, hourly paper loop, Cloud Run) built atop the most-starred artifact in its category (TauricResearch/TradingAgents, ~94.7k stars). Its genuine, verified differentiation is **decision provenance**: cited adversarial debate, a critic that rejects trades on argument quality, deterministic risk gates that demonstrably said "no" eight times on FOMC day, outcome-graded calibration with honesty guardrails, and a UI that renders refusals with equal weight. Nothing in the commercial $49–149/month band does this; nothing in open source ships it as a product.

The brutal findings:

1. **The academic ground is hostile.** No published LLM-agent trading result survives broad-universe + post-cutoff + cost-inclusive evaluation simultaneously (arXiv:2505.07078; audit 2605.19337: 1 of 19 studies models transaction costs). LLM "skill" on historical windows is substantially ticker/date memorization (2512.23847, 2603.17692). Token-matched comparisons find single strong models tie or beat multi-agent debate (2604.02460). **The platform's core architecture has never been ablated against a single-model baseline at equal token budget, and its paper lineage's benchmark numbers should be treated as contaminated until proven otherwise.**
2. **The quant/backtest layer is a decade behind open source.** NautilusTrader proves backtest→live code parity at nanosecond resolution; Lean handles corporate actions across six asset classes; vectorbt.pro ships purged/embargoed CV; FreqAI does walk-forward retraining *by default*. Our engine (`tradingagents/pro/backtest/engine.py`) has no funding costs, no partial fills, no borrow, no walk-forward, no deflated-Sharpe gate — and couples every decision bar to LLM spend.
3. **Data is the free-tier ceiling.** No point-in-time vintages, no order-book history, no options surface, four symbols. Qlib's PIT database and OpenBB's ~100 normalized providers define the open-source floor we're under.
4. **Infrastructure is deliberately pre-institutional.** `--max-instances=1` single-writer Cloud Run, JSONL files as the database, in-process metrics (`observability.py`), a hashing-trick embedder masquerading as a vector memory (`memory/embedding.py` HashingEmbedder) — each was the right lazy call for one operator, and each is a hard ceiling for anything more.
5. **The moat is real but narrow.** Explainability + honesty machinery (retro-scoring with provenance tags, calibration-as-product, venue-truth write-back) is the one dimension where this system leads *everything* surveyed, including Bloomberg (whose AI summarizes; it does not decide and it does not grade itself). The defensible business is the audit trail, not the alpha — reinforced by the SEC's Delphia/Global Predictions AI-washing actions (SEC 2024-36) which make unaudited performance claims the category's existential legal risk.

**Overall: 59/100.** Category-leading explainability wrapped around a mid-tier quant core on hobbyist infrastructure. The path to category leadership is not "more agents" — the literature is unambiguous — it is: (i) evaluation rigor nobody in the LLM-trading space has shipped (post-cutoff, cost-inclusive, memorization-audited, pass^k-tested), (ii) institutional data/backtest hygiene at bootstrap cost, and (iii) productizing the honesty machinery as the industry's reference standard for AI decision audit.

---

## 2. Competitive Landscape

### 2.1 Open source (full evidence: research pack §1)

| System | Stars | One-line position | Beats us at |
|---|---|---|---|
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | 94.7k | Our upstream; research CLI, 20+ markets, no execution | Market breadth, community, provider matrix |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB) | 71.1k | Unified API over ~100 data providers | Data breadth/normalization |
| [Freqtrade](https://github.com/freqtrade/freqtrade) | 52.7k | Crypto bot with FreqAI walk-forward ML | Live-loop reliability, walk-forward by default, exchange coverage |
| [Qlib](https://github.com/microsoft/qlib) + [RD-Agent](https://github.com/microsoft/RD-Agent) | 46.7k+14.1k | PIT quant platform + LLM factor R&D loop | Point-in-time data, factor libraries, LLM-writes-falsifiable-code |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | 25.1k | Rust event-driven, ns-resolution, backtest≡live | Execution fidelity, determinism, 18+ venue adapters |
| [Lean](https://github.com/QuantConnect/Lean) | 20.9k | Institutional multi-asset engine | Corporate actions, ~20 brokers, TB data |
| [Hummingbot](https://github.com/hummingbot/hummingbot) | 19.3k | Market-making across 50+ CEX/DEX | Microstructure execution |
| [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 15.8k | Deep-RL benchmarks | RL tooling (of debatable alpha value) |
| [VectorBT](https://github.com/polakowo/vectorbt)/.pro | 8.5k | Vectorized param sweeps; PRO: purged CV/WFO | Overfitting diagnostics at scale |
| [Jesse](https://github.com/jesse-ai/jesse) | 8.3k | Crypto backtests with funding + partial fills | Perp backtest realism |

LLM-agent repos (FinMem, FinCon, FinAgent, StockAgent, AlphaAgent, QuantaAlpha): celebrated papers, mostly dead or skeletal code — the maintained star-heavy projects are the non-LLM ones. The one emerging pattern that matters: **LLMs writing testable factor code** (RD-Agent(Q), NeurIPS 2025 D&B) rather than voting on direction.

### 2.2 Commercial (full evidence: research pack §2)

- **Bloomberg** ($31,980/yr): data+chat-network monopoly; AI = summarization/retrieval (Document Insights Apr 2025), *not* decisions. **LSEG Workspace** (~$22k): Reuters plumbing now piped into Microsoft Copilot agents.
- **TradingView** ($12.95–199.95/mo, ~100M users): charting + Pine network effects; AI Chart Copilot (beta Apr 2026) *cannot generate reliable Pine* per independent reviews. **TrendSpider** ($54–122): real user-trained ML, zero explainability. **Koyfin** ($39–299): fundamentals dashboards, no AI, no execution.
- **QuantConnect** (from ~$10/mo + nodes): institutional backtest rigor at hobbyist prices — the single hardest capability to replicate. **Alpaca** (free/$99 data): embeddable brokerage. **Composer** ($32–40): NL→live automation, RIA-registered — closest cousin; explains nothing.
- **Cautionary exits**: Capitalise.ai (free NL automation) acquired by Kraken and phased out; Kavout pivoted institutional→$16 freemium. Signal-only AI products have weak willingness-to-pay.
- **AI-native wave**: Rogo (~$2B val, banker copilot), LinqAlpha, Lumenai (agentic hedge fund), TradeZing; agentic-AI startups raised $4.74B in 12 months. None ships explainable, self-grading trade decisions to end traders.
- **Regulatory constraint**: SEC fined Delphia/Global Predictions for AI-washing ([SEC 2024-36](https://www.sec.gov/newsroom/press-releases/2024-36)) — every marketing claim this product makes must be backed by its own audit trail.

### 2.3 Crypto data & institutional practice (full evidence: research pack §3)

Free-ingestible today: Coin Metrics Community API (no key), Token Terminal (500K req/mo), Glassnode daily T1, exchange-direct funding/OI/liquidation streams (reconstructs ~80% of Coinglass's paid API), Deribit options IV (free), World Gold Council Goldhub (ETF flows + central-bank purchases — the two structural gold drivers we don't ingest). Institutional-grade concretely means: append-only replayable PIT tick stores (kdb+/ArcticDB/ClickHouse), pre-trade veto layers (FIA whitepaper), per-fill TCA with markouts, purged CV + Deflated Sharpe deployment gates (López de Prado), event-sourced state (LMAX), and MiFID RTS-6-style algo tagging + kill-switch audits.

### 2.4 Academic (full evidence: research pack §4)

The field's own referees have turned: evaluation hygiene near-zero (2605.19337), memorization empirically established (2512.23847), multi-agent gains are unnormalized-compute artifacts (2604.02460), verbalized confidence is RLHF-shaped tone (2410.09724), zero-shot time-series foundation models are negative-R² on returns (2511.18578) but competitive on **volatility** (2607.05291). The defensible frontier: outcome-graded training/calibration (Trading-R1 2509.11420 — from our own upstream group), conformal intervals feeding deterministic gates, pass^k reliability testing (τ-bench 2406.12045), and LLM-mined *falsifiable* factors.

---

## 3. SWOT

**Strengths** — decision provenance end-to-end (debate→critic→judge→gates→venue truth→outcome grading); deterministic risk gates outside the LLM; honesty machinery (retro provenance tags, blotter isolation, calibration chart); 1,331 tests; three same-day fix-verify-deploy cycles proven; hourly loop cheap (~$0.15/decision); upstream brand halo (94.7k stars).

**Weaknesses** — unproven core architecture (no token-matched ablation); free-data ceiling (no PIT, no L2, 4 symbols); backtester lacks funding/fills/walk-forward and couples to LLM cost; JSONL-as-database; single-writer Cloud Run; hashing embedder memory; in-process metrics; no TCA; verbalized confidence still displayed; single operator = bus factor 1.

**Opportunities** — empty $49–149 "explainable decisions" wedge; become the *reference implementation* for AI decision audit (regulatory tailwind post-Delphia); RD-Agent-style factor mining bolted to our gates; Goldhub/Deribit/Coin Metrics free data upgrades; Trading-R1-style RLVR on our own outcome records (we own the reward signal); upstream community funnel.

**Threats** — TradingView Copilot + broker panel adds "explain" overnight to 100M users; Composer adds reasoning; a Rogo-class team goes down-market; upstream repo commoditizes the architecture (it already is free); SEC AI-washing enforcement; LLM API price/behavior drift breaking prompt-tuned gates; markets simply not rewarding LLM judgment (ForecastBench parity ≠ tradable edge — open contradiction #1).

---

## 4. Feature Comparison Matrix

Legend: ✔ strong · ~ partial · ✘ absent. Columns: **TAP** = TradingAgents Pro; **UP** = upstream TradingAgents; **QL** = Qlib+RD-Agent; **NT** = NautilusTrader; **LN** = Lean/QuantConnect; **FQ** = Freqtrade; **OB** = OpenBB; **TV** = TradingView; **BB** = Bloomberg; **CP** = Composer; **GN** = Glassnode/Nansen; **INST** = institutional norm (Citadel/JS-class).

| Capability | TAP | UP | QL | NT | LN | FQ | OB | TV | BB | CP | GN | INST |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Market coverage (asset classes) | ~ (4 sym) | ~ | ~ | ✔ | ✔ | ~ | ✔ | ✔ | ✔ | ~ | ~ | ✔ |
| Data quality (PIT/survivorship-free) | ✘ | ✘ | ✔ | ✔ | ✔ | ~ | ~ | ~ | ✔ | ~ | ✔ | ✔ |
| Alternative data | ~ (COT/F&G) | ~ | ✘ | ✘ | ~ | ✘ | ✔ | ✘ | ✔ | ✘ | ✔ | ✔ |
| Macro data | ✔ (FRED) | ~ | ✘ | ✘ | ~ | ✘ | ✔ | ~ | ✔ | ✘ | ✘ | ✔ |
| Options data/analytics | ✘ | ✘ | ✘ | ✔ | ✔ | ✘ | ✔ | ✔ | ✔ | ✘ | ~ | ✔ |
| Order flow / LOB | ✘ | ✘ | ~ | ✔ | ~ | ~ | ✘ | ~ | ✔ | ✘ | ✘ | ✔ |
| On-chain | ~ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ | ~ | ~ | ✘ | ✔ | ✔ |
| News/sentiment | ✔ | ✔ | ✘ | ✘ | ~ | ✘ | ✔ | ✔ | ✔ | ✘ | ✔ | ✔ |
| Risk gates (deterministic, pre-trade) | ✔ | ~ | ~ | ✔ | ✔ | ✔ | ✘ | ✘ | ✔ | ~ | ✘ | ✔ |
| Execution (live, real venues) | ✘ (paper) | ✘ | ✘ | ✔ | ✔ | ✔ | ✘ | ✔ (brokers) | ✔ | ✔ | ✘ | ✔ |
| TCA / slippage measurement | ✘ | ✘ | ~ | ✔ | ✔ | ~ | ✘ | ✘ | ✔ | ✘ | ✘ | ✔ |
| Explainability of decisions | **✔✔** | ✔ | ~ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ |
| Multi-agent LLM | ✔ | ✔ | ✔ (R&D) | ✘ | ✘ | ✘ | ~ | ~ | ~ | ~ | ✘ | ~ |
| Memory / analogs | ~ (hash emb.) | ~ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔ |
| Outcome-graded calibration | **✔✔** | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔ |
| Backtesting fidelity | ~ | ✘ | ✔ | ✔✔ | ✔✔ | ✔ | ✘ | ~ | ~ | ~ | ✘ | ✔✔ |
| Walk-forward / purged CV | ✘ | ✘ | ✔ | ~ | ✔ | ✔ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔ |
| Monte Carlo | ✔ | ✘ | ~ | ~ | ✔ | ✘ | ✘ | ✘ | ✔ | ✘ | ✘ | ✔ |
| Portfolio optimization | ✘ | ✘ | ✔ | ~ | ✔ | ~ | ✘ | ✘ | ✔ | ✔ | ✘ | ✔ |
| RL | ~ (advisor stub) | ✘ | ✔ | ✘ | ~ | ~ | ✘ | ✘ | ✘ | ✘ | ✘ | ~ |
| Knowledge graph | ~ (hook only) | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✔ | ✘ | ✔ | ✔ |
| Latency class | hours | hours | mins | **ns** | ms | s | n/a | s | s | mins | n/a | **µs–ns** |
| Scalability (multi-user) | ✘ (1 writer) | n/a | ✔ | ✔ | ✔ | ~ | ✔ | ✔✔ | ✔✔ | ✔ | ✔ | ✔✔ |
| Cloud deployment | ✔ | ✘ | ~ | ~ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Visualization/dashboard | ✔ | ✘ (CLI) | ~ | ~ | ✔ | ✔ | ✔ | ✔✔ | ✔✔ | ✔ | ✔ | ✔ |
| Charting depth | ~ (14 ind.) | ✘ | ✘ | ✘ | ~ | ~ | ✘ | ✔✔ | ✔ | ✘ | ~ | ~ |
| Alerting | ✔ (price+intel) | ✘ | ✘ | ~ | ✔ | ✔ | ✘ | ✔✔ | ✔ | ~ | ✔ | ✔ |
| Mobile | ~ (PWA) | ✘ | ✘ | ✘ | ~ | ~ | ~ | ✔✔ | ✔ | ✔ | ✔ | ~ |
| Public API | ✔ (own REST) | n/a | ✔ | ✔ | ✔ | ✔ | ✔✔ | ✔ | ✔ | ~ | ✔ | ✔ |
| Broker/exchange integrations | ✘ | ✘ | ✘ | ✔ (18+) | ✔ (20) | ✔ | ✘ | ✔ (dozens) | ✔ | ✔ | ✘ | ✔ |
| Plugin/strategy marketplace | ✘ | ✘ | ~ | ~ | ✔ | ✔ | ✔ | ✔✔ | ~ | ~ | ✘ | n/a |
| Paper trading | ✔ | ~ | ~ | ✔ | ✔ | ✔ | ✘ | ✔ | ~ | ✔ | ✘ | ✔ |
| Compliance/audit trail | ✔ (hash chain) | ✘ | ✘ | ~ | ~ | ~ | ✘ | ✘ | ✔ | ✔ (RIA) | ✘ | ✔✔ |
| Observability | ~ (in-proc) | ✘ | ~ | ✔ | ✔ | ✔ | ~ | ✔ | ✔ | ✔ | ✔ | ✔✔ |
| Security/auth | ~ (allowlist) | ✘ | n/a | n/a | ✔ | ~ | ✔ | ✔ | ✔✔ | ✔ | ✔ | ✔✔ |
| Cost to run | ✔ (~$5/day) | ✔ | ✔ | ✔ | ~ | ✔ | ✔ | ~ | ✘ | ✔ | ~ | ✘ |
| Enterprise readiness | ✘ | ✘ | ~ | ✔ | ✔ | ~ | ✔ | ✔ | ✔✔ | ✔ | ✔ | ✔✔ |

**Reading:** TAP wins exactly two rows outright (explainability, outcome-graded calibration) and is competitive on gates/audit/alerting/cost. It loses every data, execution, backtest-rigor, and scale row to at least one free open-source system.

---

## 5–15. Scores (each /10, argued)

### 5. Architecture — **6/10**
For: clean contract layer (`tradingagents/contracts/` pydantic schemas everywhere), gates as deterministic code outside the LLM (`pipeline/nodes.py` event gate vetoes *before* LLM spend), append-only run/audit records, per-panel error isolation, registry-driven symbols/feeds (post-P2). Against: JSONL files as the database (`persistence.py`, `memory/JsonlStore`) — no transactions, no queries, no concurrent writers; `--max-instances=1` is a *documented invariant*, not a bug, but it caps the product at one tenant forever; state is mutated-then-repersisted rather than event-sourced (the LMAX pattern is half-adopted: we journal, we don't replay); in-process `MetricsRegistry` dies with the container. Challenge to the design: the single-writer constraint is defended as protecting memory.jsonl — an event-sourced store (litestream'd SQLite or Postgres) removes the constraint *and* the class of drift bugs the reconciliation code exists to catch.

### 6. AI — **5/10**
For: the critic gate (rejects on unrebutted strongest evidence — live-verified), retro outcome grading (exactly the literature-endorsed reward channel, cf. Trading-R1 2509.11420), prompt-injection quarantine at ingestion, model routing per stage, cost honesty in the UI. Against: **no token-matched single-model ablation exists** — the central architectural bet is unproven and the literature predicts a tie (2604.02460); verbalized confidence still drives the displayed 0–100 number (RLHF tone, 2410.09724) with empirical p(win) only recently layered beside it; memory "vectors" are a **HashingEmbedder** (`memory/embedding.py`) — hashing-trick lexical overlap, not semantics, so historical analogs are keyword matches wearing a similarity score; no pass^k reliability measurement (same market state, k runs — do verdicts flip?); no memorization audit on any historical evaluation; debate is 1 round with canned structure, closer to parallel opinions than iterative rebuttal; no hallucination detection beyond schema validation.

### 7. Quant — **4/10**
For: fixed-risk sizing with drift headroom, per-symbol VaR/CVaR, ATR levels, Kelly available, regime classifier feeding gates, deterministic scanner. Against: no purged/embargoed CV, no Deflated Sharpe or PBO gate before a config trades (López de Prado standard, library-cheap); backtests charge zero perp funding (the actual borrow cost of the main asset class), no partial fills, no impact model — the exact assumptions shown to flip reported LLM-agent returns (2606.08285); no factor layer at all (Qlib ships 300+); "signals" are LLM opinions, unfalsifiable per-decision, vs the RD-Agent pattern of mined testable factors; position sizing ignores portfolio covariance (each symbol sized independently while the correlation matrix sits unused on the Intel page).

### 8. UX — **7/10**
For: three hands-on trader-review passes ended at 8-9s for decision support/speed/risk visibility; decision provenance UI (pipeline board, debate transcript, gate waterfall) is unmatched anywhere at any price; keyboard-first; honest empty states. Against: charting is 14 indicators vs TradingView's thousands + Pine (scored 6/10 by our own reviewer); no mobile-native experience (PWA only); no multi-monitor tear-off; evidence chips don't plot to chart; information density below Bloomberg/Sierra desk standards; single-user assumptions everywhere.

### 9. Security — **6/10**
For: Google allowlist auth + HS256 session JWTs, dashboard read-only over execution (kill switch is operator-shell-only — the right philosophy), typed-confirmation flatten, prompt-injection quarantine surfaced as security events, hash-chained audit log, secrets via Cloud Run secret manager, CI on every push. Against: single-tenant trust model (no roles, no per-user isolation); no SOC2/pen-test; LLM tool-surface untested against adversarial news content beyond the quarantine heuristic; `COPY . .` ships the whole repo (tests, docs, git metadata) into the production image — needless attack/leak surface; no dependency scanning/SBOM in CI; no rate limiting on the API; supply chain = 126 locked Python deps + npm tree, unaudited.

### 10. Infrastructure — **5/10**
For: reproducible Cloud Build → Cloud Run deploys (five in one day, zero rollbacks), min-instances warm singleton, GCS-fused /data, structured logs, PWA update flow. Against: the singleton is load-bearing (see §5); SSE + 5s polling hybrid transport instead of a real push bus; no Postgres/ClickHouse — every dashboard read re-scans JSONL into memory; no cache layer beyond in-process TTLs; no infra-as-code (deploy is a bash script); no staging environment — we deployed to prod five times in a day *because there is nowhere else*; backups = whatever GCS versioning gives; monitoring = in-process counters nobody scrapes.

### 11. Explainability — **9/10**
Category-leading, full stop. Every decision: cited evidence with data refs → adversarial transcript → critic verdict → judge rationale → gate waterfall → venue truth → graded outcome → calibration. Bloomberg summarizes; TrendSpider's ML is opaque; Composer executes silently; no surveyed system closes the loop from reasoning to graded result. Docked one point: explanations reference chart structure the UI can't yet plot (evidence chips), and the confidence number shown most prominently is still the least trustworthy number on the ticket.

### 12. Production Readiness — **6/10**
For: 1,331 automated tests, CI, live paper track running unattended, same-day incident-fix-verify capability (proven three times), alert fanout (bell/Telegram/webhook). Against: bus factor 1; no staging; no on-call/alerting on infra failures (only trading alerts); no load testing; no data-corruption drills; restart semantics of the in-flight run are kill-and-lose (observed live: deploys orphaned a triggered ETH run); the demo script imports test fixtures in prod image.

### 13. Institutional Readiness — **3/10**
Against the §2.3 bar: no TCA (no arrival/markout capture even on paper fills), no PIT data vintages (FRED revisions silently rewrite history under our decisions), no purged-CV/DSR deployment gate, no per-order algo/version tagging, no RTS-6-style periodic self-assessment, no real venue connectivity, no independent risk function, no multi-user entitlements. For: pre-trade veto layer, kill switch, limits-in-config, hash-chained audit, event journal — the *cheap half* of the FIA/RTS-6 checklist is genuinely present, which is more than most retail platforms.

### 14. Innovation — **8/10**
Calibration-as-product ("the honesty metric"), retro-scoring with poisoning guardrails, venue-truth write-back, refusals as first-class UI objects, event-gate-before-LLM-spend cost design, and an explainable pipeline a regulator could actually read. Docked: the core multi-agent idea is upstream's, now commoditized (dozens of CrewAI/LangGraph clones); innovation is concentrated in the honesty layer, not the intelligence layer.

### 15. Overall — **59/100**
(Weighted: explainability and innovation can't carry data, quant, and institutional scores. The number to beat next audit: 75.)

---

## 16. Top 100 Missing Features

**Data (1–20):** 1 PIT/as-of vintage store for all macro series · 2 Deribit options IV/term structure · 3 liquidation heatmap (exchange-direct streams) · 4 Coin Metrics Community on-chain (free) · 5 Glassnode T1 daily (free) · 6 Goldhub ETF flows · 7 Goldhub central-bank purchases · 8 Token Terminal fundamentals · 9 L2 order-book snapshots (top-N depth) · 10 tick capture for traded symbols · 11 funding-rate history archive · 12 basis (perp-spot) series · 13 FX majors data · 14 index/rates futures data · 15 corporate-actions handling (for future equities) · 16 news impact scoring · 17 economic-surprise indices · 18 cross-exchange price/liquidity comparison · 19 stablecoin flow metrics · 20 realized-vol surface per symbol.

**Quant/backtest (21–40):** 21 perp-funding costs in backtests · 22 partial-fill model · 23 impact/slippage model calibrated from own fills · 24 purged/embargoed CV · 25 walk-forward harness · 26 Deflated Sharpe gate · 27 PBO estimation · 28 factor library (Alpha158-class) · 29 LLM factor-mining loop (RD-Agent pattern) · 30 portfolio-covariance-aware sizing · 31 portfolio-level VaR (not per-symbol) · 32 stress scenarios (2020-03, FTX-11, rate shocks) · 33 regime-conditional performance attribution · 34 borrow/short-cost modeling · 35 multi-strategy config comparison · 36 parameter sensitivity sweeps (vectorbt-class) · 37 benchmark-relative reporting (vs BTC/GLD buy-hold) · 38 trade-horizon distribution analytics · 39 execution-timing study (decide-at-close vs next-open sensitivity) · 40 seed/temperature variance bands on every metric.

**AI/eval (41–55):** 41 token-matched single-model ablation harness · 42 pass^k decision-stability test · 43 memorization audit (anonymized tickers/dates) · 44 post-cutoff-only eval protocol · 45 semantic embeddings for memory (replace HashingEmbedder) · 46 conformal intervals on vol forecasts feeding gates · 47 Brier/log-loss tracking on p(win) · 48 RLVR fine-tune on own outcome records · 49 hallucination/claim-verification pass (evidence refs must exist) · 50 prompt/model version pinning + regression evals per release · 51 debate-round ablation · 52 cheap-model cascade (small model screens, big model decides) · 53 self-consistency sampling on judge · 54 LLM cost budget guards per day · 55 knowledge-graph population (the hook exists, empty).

**Execution/risk (56–70):** 56 live venue connectivity (one CCXT exchange, smallest size) · 57 TCA capture (arrival/fill/markouts) even on paper · 58 smart order slicing (TWAP at minimum) · 59 reduce-only mode distinct from kill · 60 per-strategy limits hierarchy · 61 order/algo version tagging · 62 slippage-drift monitor (backtest assumption vs realized) · 63 max-drawdown auto-deleverage · 64 correlation-aware exposure caps · 65 pre-trade price-sanity band vs external reference · 66 stale-data trading halt (feed age gate) · 67 position reconciliation alarms → page operator · 68 quarterly RTS-6-style self-assessment doc generator · 69 what-if order simulator · 70 multi-account support.

**Platform/UX (71–85):** 71 evidence-chip→chart plotting · 72 custom alert builder UI (conditions exist, prefs CRUD doesn't) · 73 more indicators + user-defined formulas · 74 multi-symbol chart layouts saved per user · 75 mobile push notifications · 76 chart snapshots/sharing · 77 strategy config editor in UI · 78 backtest parameter UI (symbol/range/config) · 79 run-diff view (compare two decisions on same symbol) · 80 agent-drill-down page (per-agent history + calibration) · 81 replay of any historical day's full pipeline · 82 portfolio heatmap · 83 PDF/API export of decision audit packs · 84 dark-pool-style quiet hours config · 85 onboarding tour.

**Business/scale (86–100):** 86 multi-tenant auth + entitlements · 87 Postgres/SQLite event store · 88 staging environment · 89 external metrics (Cloud Monitoring/Grafana) · 90 public read-only API keys · 91 webhooks for decisions · 92 SOC2-track controls doc · 93 billing/subscription plumbing · 94 white-label deploy kit · 95 strategy marketplace scaffold · 96 community prompt/factor sharing · 97 SLA/status page · 98 data-license audit (yfinance ToS risk!) · 99 SBOM + dependency scanning · 100 disaster-recovery runbook with tested restore.

---

## 17. Top 50 Critical Weaknesses (ranked)

1 Unproven core architecture (no single-model ablation) · 2 backtest omits funding/fills/impact · 3 no PIT data — historical evals silently contaminated · 4 verbalized confidence displayed as primary number · 5 HashingEmbedder analogs are lexical, not semantic · 6 JSONL database (no transactions/queries/concurrency) · 7 single-writer Cloud Run ceiling · 8 4-symbol universe · 9 no live execution — zero proof decisions survive a matching engine · 10 no TCA · 11 calibration n=7 (statistically meaningless today) · 12 no purged CV/DSR deployment gate · 13 no memorization audit · 14 no pass^k stability data — hourly decisions may be coin-flips of temperature · 15 bus factor 1 · 16 no staging env · 17 in-process metrics, unscrapable · 18 deploys kill in-flight runs · 19 FRED revisions rewrite decision inputs retroactively · 20 no portfolio-level VaR · 21 sizing ignores cross-asset correlation (BTC/ETH/SOL are one trade) · 22 LLM provider drift can silently change behavior (no pinned-model regression evals) · 23 event gate calendar source is a single feed (FRED) — miss an event, trade into it · 24 no options/vol data for a gold product · 25 no L2/order-flow anywhere · 26 yfinance ToS/reliability risk in production · 27 Delta Exchange geo-gating single point of failure for live prices · 28 alert engine state resets on restart (re-fires) · 29 no rate limiting/abuse protection on API · 30 whole repo shipped in prod image · 31 no SBOM/dep scanning · 32 demo/test fixtures importable in prod · 33 no load tests (SSE fanout unknown ceiling) · 34 knowledge-graph hook unpopulated · 35 RL advisor modules unvalidated (literature says likely worthless for alpha) · 36 charting breadth (14 indicators, no scripting) · 37 no mobile push · 38 debate is 1 round — "adversarial" is mostly parallel · 39 critic can be verbose-argument-gamed (rejects on rebuttal *presence*, not quality) · 40 judge sees vote tally — anchoring bias unstudied · 41 hourly cadence arbitrary (no event-driven triggers) · 42 no benchmark-relative honesty (vs buy-and-hold BTC the loop may lose) · 43 gold COT weekly staleness not surfaced in agent prompts · 44 news feed is Yahoo-only (single narrative source) · 45 no data-quality quarantine beyond injection heuristic (bad prints reach agents) · 46 paper venue fills at close ± flat bps — optimistic vs spread reality · 47 no monthly kill-switch test ritual · 48 marketing claims legally exposed until audit-pack export exists (Delphia) · 49 open-source upstream can ship the same features to 94.7k stargazers · 50 single LLM provider default (DeepSeek) concentration risk.

## 18. Top 50 Strengths

1 End-to-end decision provenance (unique in market) · 2 outcome-graded calibration with honesty guardrails · 3 deterministic gates outside the LLM · 4 event gate vetoes before LLM spend · 5 critic rejection on argument quality (live-verified) · 6 venue-truth write-back (no phantom fills) · 7 retro-scorer with provenance/idempotency/no-lesson guards · 8 refusals as first-class UI · 9 hash-chained audit log · 10 kill-switch philosophy (browser can't halt/unhalt) · 11 typed-confirmation flatten · 12 prompt-injection quarantine surfaced as security events · 13 1,331 tests green · 14 CI on push · 15 contracts-first schemas · 16 registry-driven symbols/feeds · 17 per-symbol feed fallbacks (Delta→Binance) · 18 multi-symbol rotation at flat LLM cost · 19 ~$0.15/decision economics · 20 daily loss budget visible + enforced · 21 paper daily order cap · 22 pre-trade validation with veto · 23 limits in config · 24 3D pipeline board (explainability UX) · 25 debate transcript with citations · 26 gate waterfall · 27 agent leaderboard + calibration chart · 28 p(win)/EV/median-hold on tickets · 29 honest empty states everywhere · 30 data dictionary on intel tiles · 31 alert dedupe + condition alerts · 32 deterministic scanner (zero-LLM) · 33 dashboard backtest replay (isolated memory) · 34 journal writes its own lessons · 35 run→trade→outcome linkage · 36 regime drift flags · 37 command palette + keyboard-first UX · 38 PWA offline shell · 39 same-day fix-verify-deploy capability (proven ×3) · 40 structured logs · 41 GCS-fused durable state · 42 warm singleton (no cold-start flicker) · 43 Telegram/webhook alert sinks · 44 CSV/PDF exports · 45 layout presets + saved views · 46 upstream brand (94.7k stars) as funnel · 47 free-data cost floor near zero · 48 trader-review documentation culture (three scored passes) · 49 evals harness exists (`pro/evals/`) to build regression tests on · 50 Apache-2.0 upstream = legal freedom to commercialize.

## 19. Top 50 Opportunities

1 Own the "AI decision audit" category before regulation forces it on everyone · 2 token-matched ablation as a *published benchmark* (first in field) · 3 RLVR fine-tune on own outcomes (Trading-R1 recipe, we own the reward data) · 4 RD-Agent-style factor mining feeding our gates · 5 conformal vol intervals → gates · 6 free data sprint (Goldhub/Deribit/CoinMetrics/TokenTerminal) · 7 liquidation heatmap from exchange streams · 8 one live exchange at dust size = existence proof · 9 TCA-on-paper as marketing ("we measure our own slippage") · 10 decision-audit PDF packs for RIAs/compliance buyers · 11 $99 wedge pricing (empty band per commercial pack) · 12 upstream PR funnel (contribute gates back, harvest users) · 13 publish the honesty spec as an open standard · 14 ForecastBench-style live public track record page · 15 pass^k reliability score as a product metric · 16 multi-tenant SaaS after event-store migration · 17 white-label for prop-firm education desks · 18 broker-affiliate revenue (Composer model) · 19 FX majors expansion (OANDA adapter half-exists) · 20 equities via Alpaca paper · 21 options analytics for gold (Deribit + CME data) · 22 strategy config marketplace · 23 prompt/agent marketplace with calibration-gated listings · 24 API keys for quants (headless decisions) · 25 webhook→TradingView alert bridge (ride their network) · 26 Pine export of levels · 27 Numerai-style staked signal tournament on our gates · 28 educational content from real debate transcripts · 29 "explainable copilot" B2B licensing to brokers post-Capitalise · 30 Kaggle-style public eval of LLM trading claims (we host the referee) · 31 agent-drill-down analytics as premium tier · 32 institutional pilot: audit-trail-as-a-service · 33 SOC2-lite trust page · 34 open-source the retro-scorer (citation magnet) · 35 academic collaboration on memorization audits · 36 regime-conditional strategy switching · 37 portfolio optimizer over multi-symbol book · 38 event-driven triggers (CPI print → immediate run) · 39 news-impact scored feed as standalone product · 40 mobile push + watch complications for position state · 41 multi-model consensus pricing tier · 42 on-prem deploy kit for funds · 43 EU MiFID-aligned audit exports as differentiator · 44 partnerships with data vendors for bundled tiers · 45 affiliate/licensed Coinglass tier when revenue exists · 46 dark-launch equities coverage to test demand · 47 community-contributed evidence agents (calibration-gated) · 48 "bring your own LLM key" tier (zero marginal cost) · 49 annual transparency report (all decisions, all outcomes) · 50 acquisition positioning vs TradingView/Composer as the explainability tuck-in.

## 20. Top 50 Risks

1 SEC AI-washing enforcement (Delphia precedent) · 2 LLM trading edge may not exist post-cutoff (2505.07078) · 3 TradingView adds explanations to Copilot · 4 upstream ships a hosted product · 5 model-provider behavior drift breaks calibrated prompts · 6 DeepSeek/API geopolitical or pricing shock · 7 yfinance ToS enforcement kills gold EOD path · 8 Delta Exchange access/geo changes kill live prices · 9 FRED revisions invalidate historical decisions quietly · 10 Binance sampled liquidation stream misleads magnitude-based signals · 11 single operator incapacitated = product dead · 12 JSONL corruption event with no tested restore · 13 Cloud Run regional outage = total outage · 14 calibration n grows slowly → honesty chart stays embarrassing · 15 retro-scorer bug pollutes the one trusted metric · 16 correlated crypto book (BTC/ETH/SOL) concentrates what looks diversified · 17 paper-venue optimism builds false confidence pre-live · 18 gates over-fit to gold-FOMC pattern, miss crypto-native events (ETF decisions, halvings) · 19 injection quarantine bypassed by novel attack → poisoned debate · 20 prompt/repo leak reveals full strategy surface (COPY . .) · 21 API scraped/abused (no rate limits) · 22 GDPR/data-residency if EU users arrive · 23 marketing screenshot with unaudited return = legal exposure · 24 hourly cadence exploited/front-run if decisions become public · 25 strategy homogenization with other LLM traders (2605.19337 warning) · 26 token costs spike with reasoning-model pricing · 27 SSE fanout collapse at modest user counts · 28 npm/pip supply-chain compromise · 29 Google auth allowlist misconfig locks out/lets in · 30 GCS fuse latency spikes corrupt write patterns · 31 test-fixture import in prod becomes an RCE-ish surface · 32 open-sourcing honesty spec arms competitors faster than it brands us · 33 Numerai/Composer pivot into explainability with 100× resources · 34 crypto winter kills retail willingness-to-pay · 35 gold data licensing (CME) if we grow · 36 Telegram sink leaks decisions to wrong chat · 37 stale-feed trading (no feed-age gate) on vendor incident · 38 replay endpoint DoS (sync compute) · 39 knowledge-graph hook rots unused · 40 RL modules mislead roadmap effort (literature: skip) · 41 over-indexing on gold expertise as crypto users dominate · 42 fake "verified" claims by copycats dilute the audit-trail brand · 43 key-person prompt knowledge undocumented · 44 multi-tenant migration breaks single-writer assumptions subtly · 45 regulatory reclassification of signals as advice in some jurisdictions · 46 chart library license change (lightweight-charts) · 47 exchange API breaking changes mid-position · 48 LLM outage during open position with no protective-order fallback at venue · 49 backfill/backtest confusion by users (simulation vs record) despite labels · 50 burnout: solo-operator cadence of this session is not sustainable.

---

## 21. Roadmap

### Phase 1 — Quick wins (2 weeks)
| Item | Value | Complexity | Effort | ROI |
|---|---|---|---|---|
| pass^k stability test (same snapshot ×10, measure verdict flips) | Proves/kills core premise | Low | 2d | Extreme |
| Token-matched single-model ablation harness | Same | Low | 3d | Extreme |
| Perp funding costs in backtest + paper P&L | Realism | Low | 2d | High |
| TCA capture on paper fills (arrival/markouts) | Institutional habit #4 | Low | 2d | High |
| Free data: Deribit IV, Coin Metrics, Goldhub ETF+CB, Glassnode T1 | Signal/dollar | Low | 4d | High |
| Feed-age trading halt + alert-state persistence | Safety | Low | 1d | High |
| Slim prod image (drop tests/docs), SBOM + pip-audit in CI | Security | Low | 1d | Med |
| Brier score on p(win) shown next to calibration | Honesty | Low | 1d | Med |

### Phase 2 — Professional-grade (2 months)
Event-store migration (SQLite+litestream or Postgres; replayable state; unlocks multi-instance) · purged CV + Deflated Sharpe gate wired into backtest UI · memorization-audited, post-cutoff-only eval protocol published · semantic embeddings for memory (small local model) · portfolio-level VaR + correlation-aware caps · event-driven triggers (calendar prints, vol spikes → immediate run) · custom alert builder UI · FX majors (generalize OANDA adapter) · staging environment + Cloud Monitoring metrics · evidence-chip→chart plotting · liquidation reconstruction from exchange streams.

### Phase 3 — Institutional-grade (6 months)
One live exchange, dust-size, reduce-only ladder (existence proof + real TCA) · PIT vintage store for all macro/positioning series · RD-Agent-style factor mining loop feeding gates (LLM writes falsifiable code) · conformal vol intervals as gate inputs · multi-tenant auth + entitlements · decision-audit export packs (RTS-6-flavored) · quarterly self-assessment generator · options analytics (gold vol surface) · order slicing (TWAP) · public read-only API + webhooks · SOC2-track controls documentation.

### Phase 4 — World-class (12 months)
RLVR fine-tune on own outcome corpus (Trading-R1 recipe) · public live track-record page (ForecastBench-style, contamination-proof by construction) · strategy/prompt marketplace with calibration-gated listings · equities via Alpaca · portfolio optimizer across book · mobile push · white-label/on-prem kit · 20+ symbols · independent security audit.

### Phase 5 — 10× innovations nobody offers
1. **The Referee**: a hosted, contamination-proof public benchmark where ANY AI trading system submits decisions pre-outcome and gets graded — we own the leaderboard the field lacks (2605.19337 shows nobody can self-grade honestly).
2. **Audit-trail-as-a-service**: decision-provenance API for other AI trading products to meet post-Delphia scrutiny — sell the compliance layer to competitors.
3. **Calibration-priced subscriptions**: fees scale with realized p(win) accuracy — the first product whose price is its own honesty metric.
4. **Adversarial market twin**: agents trade against a simulated crowd of LLM traders (2504.10789) to detect strategy homogenization before deploying.
5. **Explainability diffs**: "what changed the machine's mind since yesterday" as a daily institutional brief — provenance made temporal.

---

## 22. Ordered Engineering Backlog (top 20)
1 pass^k harness · 2 single-model ablation · 3 funding costs · 4 TCA capture · 5 free-data adapters (4) · 6 feed-age gate · 7 alert-state persistence · 8 image slimming + SBOM · 9 Brier display · 10 event store migration · 11 purged CV/DSR gate · 12 semantic embeddings · 13 eval protocol + memorization audit · 14 portfolio VaR/corr caps · 15 event-driven triggers · 16 alert-builder UI · 17 staging env + external metrics · 18 evidence-chip plotting · 19 FX adapter generalization · 20 live-exchange dust pilot.

## 23. Research Backlog
1 Does debate beat one model at equal tokens on OUR pipeline? (nobody has published this for finance) · 2 pass^k vs temperature/model — decision stability curve · 3 memorization exposure of DeepSeek on 2024-26 gold/crypto history · 4 conformal vol intervals vs GARCH/HAR as gate inputs (2607.05291 says vol is winnable) · 5 critic-gaming: can verbose rebuttals defeat the unrebutted-evidence check? · 6 judge anchoring on vote tally (ablate tally visibility) · 7 optimal cadence: hourly vs event-driven trigger value · 8 LLM-mined factors on our 4 symbols vs Alpha158 baselines · 9 crowding: correlation of our decisions with public LLM-trader baselines · 10 RLVR data-efficiency: how many graded outcomes before fine-tune beats prompting?

## 24. Architecture (text diagrams)

**Current:** `feeds(free APIs) → SnapshotBuilder → LangGraph[prepare→gate→teams(∥)→debate→critic→judge→PM→approval→exec] → PaperVenue → JSONL(memory/runs/audit on GCS-fuse) → FastAPI(SSE+REST) → React PWA`; singleton Cloud Run; hourly rotation ×4 symbols; Cloud Build deploys.

**Target (Phase 2-3):** `feeds → PIT vintage store (event log) → trigger bus (cron + calendar + vol events) → pipeline workers (N, stateless; state via event store) → decision log (append-only, replayed to views) → {paper venue + live dust venue} → TCA recorder → outcome grader → calibration/eval service (pass^k, ablations, Brier) → API gateway (multi-tenant, rate-limited) → web/mobile/webhooks`; SQLite+litestream→Postgres; external metrics; staging mirror.

## 25. Future Research Directions
Outcome-graded (verifiable-reward) decision models as the successor to prompted debate · conformal risk control for gate thresholds · LLM factor mining with decay regularization · market simulation for crowding detection · causal attribution of P&L to evidence items (which agent's evidence actually pays?) · privacy-preserving multi-tenant memory.

## 26. Technologies to Monitor (5 years)
Reasoning-model price/perf curves (changes the ablation answer yearly) · RLVR/agentic-RL tooling maturation · time-series foundation models for vol (not returns) · ArcticDB/ClickHouse-class open tick stores · MCP as the tool-integration standard (Numerai already ships one) · conformal prediction libraries · EU AI Act + SEC algorithmic-advice rulemaking · exchange-native sub-account APIs for safe live pilots · WebGPU charting engines · open-weight frontier models (provider-risk hedge).

---

## Moat Analysis (Phase 14)

**Easy to copy:** the multi-agent debate itself (upstream is free, 94.7k stars), the dashboard aesthetics, condition alerts, scanner. **Hard to copy:** the integrated honesty machinery (retro provenance, blotter isolation, venue-truth write-back — subtle correctness, three bugs deep as we learned live), the accumulating *graded decision corpus* (data moat: nobody can backfill honest outcomes they never recorded), a published contamination-proof track record (time is unforgeable). **Nearly impossible to copy:** the referee position — if the public benchmark/audit-standard play lands first, competitors join our leaderboard or look evasive. **Network effects available:** calibration-gated marketplace (quality flywheel), upstream community funnel, webhook/TradingView bridges. **Defensibility increases by:** shipping the eval protocol publicly, accumulating graded outcomes daily, and converting honesty into a compliance product the rest of the category must buy or rebuild.

## Product Strategy (Phase 15)

Position: **"the AI trading terminal that shows its work and grades itself"** — sell trust, not alpha (legal posture post-Delphia demands it anyway). Segments: prosumer/pro traders at **$99/mo** (empty wedge per commercial pack: Composer $40 explains nothing, Tickeron $250 is signal noise); prop-education desks white-label; RIAs/funds buy audit packs + API ($500+/mo); institutions pilot audit-trail-as-a-service. Licensing: keep core Apache-2.0-compatible with upstream, closed honesty/product layer; open-source the retro-scorer + eval protocol as standard-setting. API strategy: read-only decision/audit API first (safe), execution API never without regulatory counsel. Marketplace: calibration-gated agents/strategies (listings must publish graded records — the moat as gatekeeper). Do NOT chase: HFT latency, options market-making, DRL alpha, TSFM return prediction — the evidence pack says these are dead ends for this team size.

---

## Ranked Top-25 Highest-ROI Improvements

| # | Improvement | Impact | Effort | Risk | Measurable result |
|---|---|---|---|---|---|
| 1 | pass^k decision-stability harness | High | 2d | Low | flip-rate % per snapshot; kills or validates hourly cadence |
| 2 | Token-matched single-model ablation | High | 3d | Low | Δaccuracy & Δcost vs debate; decides the architecture |
| 3 | Funding costs in backtest+paper | High | 2d | Low | realized-vs-assumed P&L gap closes |
| 4 | Post-cutoff, cost-inclusive eval protocol (published) | High | 1w | Low | first credible LLM-trading eval in field |
| 5 | Memorization audit (anonymized re-runs) | High | 3d | Low | Δperformance anonymized vs named |
| 6 | TCA on paper fills | High | 2d | Low | slippage-drift chart |
| 7 | Free-data sprint (Deribit/CoinMetrics/Goldhub/TokenTerminal) | High | 4d | Low | 4 new gate inputs, $0 |
| 8 | Event store migration (SQLite+litestream) | High | 2w | Med | multi-instance unlocked; restore drill passes |
| 9 | Purged CV + Deflated Sharpe deployment gate | High | 1w | Low | PBO/DSR on every config |
| 10 | Semantic embeddings for memory | Med | 3d | Low | analog similarity human-rated ↑ |
| 11 | Feed-age halt + alert-state persistence | Med | 1d | Low | zero stale-data trades / zero re-fires |
| 12 | Portfolio VaR + correlation caps | High | 1w | Med | book-level risk number on strip |
| 13 | Event-driven triggers (calendar/vol) | Med | 1w | Med | decision latency to events ↓ hours→mins |
| 14 | Brier score + verbalized-confidence demotion | Med | 1d | Low | honesty metric hardened |
| 15 | Live dust-size exchange pilot | High | 3w | Med | first real fills + real TCA |
| 16 | Liquidation heatmap from exchange streams | Med | 1w | Low | Coinglass parity signal free |
| 17 | Custom alert builder UI | Med | 1w | Low | retention feature; prefs CRUD |
| 18 | Staging env + external metrics | Med | 3d | Low | zero prod-first deploys |
| 19 | Image slimming + SBOM + pip-audit | Med | 1d | Low | supply-chain surface ↓ |
| 20 | Evidence-chip→chart plotting | Med | 4d | Low | explainability loop closes visually |
| 21 | Decision-audit PDF export | Med | 3d | Low | first compliance-buyer artifact |
| 22 | FX majors expansion | Med | 2w | Med | universe 4→8; calibration accrual ×2 |
| 23 | LLM factor-mining pilot (RD-Agent pattern) | High | 3w | High | factors with OOS IC vs Alpha158 baseline |
| 24 | Conformal vol intervals → gates | Med | 2w | Med | gate coverage guarantees |
| 25 | Public live track-record page | High | 1w | Med* | the marketing asset nobody can fake (*legal review first) |

## Unknown Unknowns
1. **Does ForecastBench-level judgment transfer to markets at all?** The field's central contradiction — superforecaster-parity AI vs no-market-beating agents — is unresolved; our own graded corpus is the only instrument we'll trust. 2. **Decision stability under provider updates** — no one has measured how silently model upgrades shift a calibrated pipeline; needs pinned-model regression evals we don't have. 3. **Crowding**: how correlated are we with every other DeepSeek/GPT-prompted trader? Unmeasurable until we test against public LLM baselines. 4. **Critic gameability** — adversarial-prompt red-teaming of the debate has never been attempted. 5. **The paper-venue optimism gap** — unknowable until the live dust pilot; every assumption (fills at close, 3bps slippage) is a guess wearing a config value. 6. **Legal perimeter of publishing graded decisions** — securities-advice classification varies by jurisdiction; needs counsel before the track-record page or marketplace. 7. **User demand for explanations** — our thesis; Capitalise/Kavout suggest retail may not pay for *any* AI features — the audit-trail buyer may be institutional, not the trader we designed for.

---

*Evidence base: four research packs (open-source, commercial, crypto/institutional, academic — 100+ cited sources inline above), plus first-hand code audit and three live production review sessions (TRADER_REVIEW.md) during 16–17 July 2026. Compiled 27 July 2026.*
