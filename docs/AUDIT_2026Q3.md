# TradingAgents Pro — Independent Competitive Audit, Q3 2026 (v2)

**Audit date:** 9 August 2026, **deep revision 10 August 2026** ·
**Commit:** `63bb297` · **Method:** verify-don't-credit — every internal claim
checked against code, tests, or live artifacts I could cite; roadmap prose was
treated as marketing, not evidence. Written independently of
`docs/COMPETITIVE_TEARDOWN.md` (27 Jul, 59/100); the two are reconciled in
Appendix A only after scoring.

**v2 changelog (what moved and why):**
- **New evidence folded in:** the v1 #1 recommendation (stability k≥30 +
  ablation n≥100 re-measure) was attempted on 10 Aug and **failed on provider
  capacity** — claude-cli hit its session limit at the first call (776
  errors, 0 successful runs, ~20 h of harness time wasted), and every
  configured API fallback was simultaneously dead (DeepSeek 402, OpenAI no
  credits, Google 403, xAI no credits). Details in §10/§12; it moves the
  Infrastructure score.
- **Scores:** Infrastructure 4.5 → **4.0** (provider capacity is decision
  infrastructure, and it failed empirically). All other scores re-verified
  and held. **Overall 62 → 61.** More was learned; the score went down —
  that is what verify-don't-credit produces.
- **Citation density:** every competitor cell class in §2/§4 now carries a
  source URL (dated 10 Aug 2026); every TAP matrix row carries an internal
  evidence citation (file, test, or audit artifact) in the footnote table
  below the matrix.
- **Closed-since-v1 annotations:** items fixed after the v1 cut are marked
  with commit hashes rather than silently deleted.

---

## 1. Executive summary

**Overall: 61/100** (v1: 62 — revised down on new evidence, see changelog).
The system is a genuinely category-leading *explainability and safety* product
wrapped around an AI thesis that its own evidence does not support, running on
infrastructure that cannot survive its own operator closing a laptop lid — nor,
as of 10 Aug, fund the single experiment that would validate its own
architecture.

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
agreement. The 2025–26 literature, re-verified for v2, is *conditionally*
consistent with that result — and the conditions matter:

- [M3MAD-Bench](https://arxiv.org/abs/2601.02854) (ACM MM 2026) finds debate
  effectiveness is task-dependent, with **adversarial (bull/bear-style)
  debate the weakest family** — precisely TAP's design *(v1 overstated this
  as "ineffective across many domains"; corrected)*.
- [Voting or Consensus?](https://arxiv.org/abs/2502.19130) (ACL 2025
  Findings) is two-sided: with strong models, same-model debate often fails
  to beat single-agent CoT at 5–10× the compute and extra rounds *reduce*
  performance — but the right decision protocol makes debate win by up to
  +13.2% on reasoning tasks *(v1 cited only the negative half; corrected)*.
- The "249 improved / 236 worsened" figure is real —
  [arXiv:2606.16047](https://arxiv.org/abs/2606.16047) (KES 2026) — but it
  is an **argument-mining study, not finance**, and its actual headline is
  that **confidence-gated selective debate wins**: debate only the
  low-confidence cases *(v1 omitted both caveats; corrected — and the gating
  result is directly actionable here, see R1b)*.
- [StockBench](https://arxiv.org/abs/2510.02209) stands as characterized:
  most state-of-the-art LLM agents fail to beat buy-and-hold in
  contamination-free evaluation (some do; "most" is the paper's word).
- Two findings v1 missed cut *against* TAP's current design specifically:
  [Stop Overvaluing Multi-Agent Debate](https://arxiv.org/abs/2502.08788)
  finds **model heterogeneity is the "universal antidote"** that makes
  debate work — TAP runs all 59 agents on the *same* model; and
  [The Cost of Consensus](https://arxiv.org/pdf/2605.00914) finds
  homogeneous same-model debate loses to isolated self-correction because
  agents share correlated errors.

So the honest position is: this product's differentiator is *not* that 59
agents produce better decisions — there is no evidence for that and mounting
evidence against it. Its differentiator is that **it is the only system in the
category that can prove what it decided, why, on what data, under which model
version, and what happened next.** That is a real, defensible, and currently
unmatched moat. The roadmap should stop trying to win on alpha and start
winning on *provable process* — and should run the experiment that either
rescues or retires the debate architecture.

**New since v1 — the experiment was attempted and could not run.** On 10 Aug
the k≥30 stability + n≥100 ablation re-measure was launched on the production
provider. It failed **at the first model call** (claude-cli session limit;
the eval, the live campaign, and the operator's own tooling share one
subscription) and burned ~20 h retrying before being killed — with the further
finding that a fully-failing stability run would have produced a *fake-clean*
result (identical runs because identically refused) had it not been inspected.
Every configured API fallback was simultaneously unfunded or unauthorized
(DeepSeek 402, OpenAI 429-no-credits, Google 403, xAI 403). Measured
throughput on the production provider: **≥11.5 min per pipeline run** at 8
workers (`63bb297` adds `--agent-workers` to tune this), putting the full
experiment at ~31 h there vs well under an hour and a few dollars on any
funded metered API. The decisive experiment for a system that trades money is
currently gated on roughly **$10 of API credit** — that fact is itself audit
evidence, and it is why the Infrastructure score moves down in v2.

**One-line verdict:** *World-class decision provenance; unproven decision
quality; hobbyist decision infrastructure. Fix the middle one with evidence,
not features.*

---

## 2. Competitive landscape

### 2.1 Open source

All facts checked 2026-08-10 against repos/docs.

| System | Where it beats TAP | Where TAP beats it |
|---|---|---|
| [TradingAgents (upstream)](https://github.com/TauricResearch/TradingAgents) — ~97.1k★, v0.3.1 (Jul 2026), Apache-2.0 | Mindshare, 12+ LLM providers, simpler onboarding | Contracts, safety layer, real execution (upstream trades a *simulated exchange* only, [README](https://github.com/TauricResearch/TradingAgents#readme)), audit, calibration — TAP is the hardened fork |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) — ~25.4k★, v1.231.0 (Aug 2026; Rust-native v2 RC), LGPL-3.0 | Rust core, [nanosecond-resolution tick/L2/book backtests with live/backtest parity](https://github.com/nautechsystems/nautilus_trader#readme), [18 stable venue integrations](https://nautilustrader.io/docs/latest/integrations/) | Explainability, LLM reasoning, calibration record |
| [Qlib](https://github.com/microsoft/qlib) (~47.3k★) + [RD-Agent](https://github.com/microsoft/RD-Agent) (~14.2k★, v0.8.0 Nov 2025, NeurIPS 2025) | Industrial factor infra, PIT database, [LLM-automated factor/model R&D loops](https://github.com/microsoft/RD-Agent#readme) | Execution, safety, UI, decision audit |
| [Freqtrade](https://github.com/freqtrade/freqtrade) — ~53.1k★, monthly releases, GPL-3.0 | Live track records, 12+ exchanges via ccxt, hyperopt, [FreqAI](https://www.freqtrade.io/en/stable/freqai/) | Reasoning transparency, risk gates, institutional controls |
| [Lean / QuantConnect](https://github.com/QuantConnect/Lean) — ~21.1k★, Apache-2.0 | [11 asset classes](https://www.quantconnect.com/docs/v2/writing-algorithms/securities/asset-classes), options backtests, [21 live brokers](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/brokerages) | Explainability, agentic reasoning |
| [ai-hedge-fund (virattt)](https://github.com/virattt/ai-hedge-fund) — **~62.7k★**, v2.2.0 (Aug 2026), MIT | Mindshare (2nd-largest LLM-trading repo); persona-agent design | Everything real: it is [explicitly educational and places no trades](https://github.com/virattt/ai-hedge-fund#readme) — yet its star count defines the retail imagination TAP competes with |
| [TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) — ~31k★ fork | China-market reach (A-shares, Chinese LLMs) | Same as upstream |
| [Lumibot](https://github.com/Lumiwealth/lumibot) — ~1.9k★, v4.5.83 (Aug 2026) | Broad broker coverage, simple API | Everything on the rigor axis |

Context rows: [OpenBB](https://github.com/OpenBB-finance/OpenBB) (~71.7k★)
[sunset its terminal](https://openbb.co/blog/sunsetting-openbb-terminal-why-how-and-what-now/)
and is now a data platform + commercial AI workspace — a data *supplier*
class, not a terminal competitor. [Backtrader](https://github.com/mementum/backtrader)
(~22.8k★) is effectively unmaintained (last push Aug 2024).
[VectorBT](https://github.com/polakowo/vectorbt) OSS is the community edition
of a proprietary PRO product. [Hummingbot](https://github.com/hummingbot/hummingbot)
(~19.4k★) and [FinRL](https://github.com/AI4Finance-Foundation/FinRL)
(~16k★) own the market-making and DRL-research lanes respectively.

TAP wins **two rows outright across the whole open-source field**:
decision provenance (hash-chained audit + version stamps + export packs) and
outcome-graded calibration. It loses every data-depth, execution-latency,
backtest-fidelity, and scale row to at least one free alternative.

### 2.2 Commercial

Verified pricing (10 Aug 2026): Bloomberg Terminal ≈ **$31,980/yr** single
seat ([costbench](https://costbench.com/software/financial-data-terminals/bloomberg-terminal/));
[TradingView](https://www.tradingview.com/pricing/) $14.95–239.95/mo;
[Koyfin](https://www.koyfin.com/pricing/) $39–299/mo;
[TrendSpider](https://trendspider.com/pricing/) ~$50–118/mo annual;
[Composer](https://www.composer.trade/pricing) $32/mo;
[QuantConnect](https://www.quantconnect.com/pricing) free tier + paid seats;
[Sierra Chart](https://www.sierrachart.com/index.php?page=doc%2FPackages.php)
$26–56/mo; [Bookmap](https://bookmap.com/en/packages-comparison) $19–99/mo +
data fees. A retail stack of Koyfin + TradingView + a news-AI tool covers most
non-execution Bloomberg use for **under $100/mo**.

The incumbents are moving on AI: Bloomberg shipped AI transcript/news
summaries and Document Search & Analysis
([late 2025](https://a-teaminsight.com/blog/bloomberg-launches-ai-powered-research-tool-for-terminal-users/));
TradingView launched a Chart Copilot beta (Apr 2026,
[secondary source](https://blog.traderspost.io/article/tradingview-ai-features-chart-copilot-documents-news));
[Alpaca shipped an official MCP server](https://alpaca.markets/) for
LLM-agent trading. And a funded wave is arriving:
[Rogo raised a $160M Series D](https://www.prnewswire.com/news-releases/rogo-raises-160m-series-d-to-scale-the-agentic-platform-for-finance-302756546.html)
(Apr 2026, agentic finance for 250+ institutions),
[Perplexity launched a professional-finance workspace](https://gadgetbond.com/perplexity-computer-for-professional-finance-launch/),
and upstream Tauric teased its own Terminal product alongside
[Trading-R1](https://arxiv.org/abs/2509.11420).

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
The citable bar: [Jane Street's correctness-as-risk-control culture](https://blog.janestreet.com/why-ocaml/);
[HRT's kernel-level latency engineering](https://www.hudsonrivertrading.com/hrtbeat/low-latency-optimization-part-1/);
[Databento on point-in-time correctness](https://databento.com/blog/instrument-definitions)
(non-PIT reference data silently injects look-ahead — TAP's P3-02 vintages
answer exactly this); and the canonical execution-safety citation,
[the SEC's Knight Capital order](https://www.sec.gov/litigation/admin/2013/34-70694.pdf)
— one un-updated server, no kill-switch, ~$460M in 45 minutes, and the
regulatory floor for pre-trade risk controls ever since. TAP has genuinely
institutional *instincts* (kill switches in three layers, fail-closed gates,
hash-chained audit, arming ceremonies) implemented at hobbyist *scale* (one
process, one laptop, one shared token).

### 2.4 Academic (last 24 months)

**Against the naive multi-agent thesis** (all re-verified 10 Aug 2026):
- [StockBench](https://arxiv.org/abs/2510.02209) — contamination-free
  multi-month evaluation; most SOTA LLM agents fail to beat buy-and-hold.
- [DeepFund](https://arxiv.org/abs/2505.11065) — historical backtests let
  LLMs "time travel" via training-corpus leakage; in live post-cutoff
  evaluation even SOTA models lose money. (Upstream TradingAgents'
  [positive results](https://arxiv.org/abs/2412.20138) are backtest-mode —
  exactly the evaluation these papers indict. Contradiction surfaced.)
- [M3MAD-Bench](https://arxiv.org/abs/2601.02854) — adversarial debate is
  the weakest MAD family; effectiveness is task-conditional.
- [Stop Overvaluing Multi-Agent Debate](https://arxiv.org/abs/2502.08788) —
  MAD often loses to CoT/self-consistency at far more compute; **model
  heterogeneity is the consistent fix**.
- [The Cost of Consensus](https://arxiv.org/pdf/2605.00914) — homogeneous
  same-model agents share correlated errors; isolated self-correction wins.
- [Talk Isn't Always Cheap](https://arxiv.org/html/2509.05396v1) —
  sycophancy/conformity can flip initially-correct agents to wrong consensus.

**For debate under the right conditions** (the design space TAP should test):
- [Voting or Consensus?](https://arxiv.org/abs/2502.19130) — protocol choice
  can make debate win by +13.2%; more rounds hurt.
- [Conditional Effectiveness of MAD as test-time scaling](https://arxiv.org/abs/2505.22960)
  — debate helps under identifiable difficulty/diversity conditions.
- [Confidence gating](https://arxiv.org/abs/2606.16047) — debate only the
  low-confidence cases; full debate ≈ zero net benefit (249 vs 236).
- [Diverse Evidence, Better Forecasts](https://arxiv.org/pdf/2607.01661) —
  deliberation helps **under information asymmetry** — TAP's team-sliced
  snapshots create exactly this; directly testable (R1).

**Adjacent, verified:**
- [TSFMs for return forecasting](https://arxiv.org/abs/2606.27100) —
  zero-shot foundation models lose to boosted-tree baselines; the exception
  is [realized volatility](https://arxiv.org/pdf/2607.05291), where TSFMs
  approach HAR-class parity (validating TAP's HAR+conformal choice).
- [Conformal Kelly](https://arxiv.org/abs/2608.01494) — conformal interval
  width scaling fractional-Kelly sizing; independent support for TAP's
  conformal-gated sizing direction.
- [Trading-R1](https://arxiv.org/abs/2509.11420) /
  [Trade-R1](https://arxiv.org/pdf/2601.03948) / [Fin-R1](https://arxiv.org/abs/2503.16252)
  — RLVR applied to trading reasoning is now an active lane, including by
  upstream Tauric itself.
- [Mind the Confidence Gap](https://arxiv.org/abs/2502.11028),
  [decision-faithfulness of verbal confidence](https://arxiv.org/html/2601.07767v1),
  [LLM-judge overconfidence](https://arxiv.org/abs/2508.06225) — LLM
  confidence numbers are systematically miscalibrated and often don't govern
  the model's own decisions; TAP weights consensus by exactly these numbers.
- [Toward Reliable Evaluation of LLM-Based Financial Multi-Agent Systems](https://arxiv.org/html/2603.27539v1)
  — the field under-reports transaction and compute cost per decision.
- [Mitigating Look-Ahead Bias in Financial Backtesting with LLMs](https://arxiv.org/html/2605.24564)
  — prompt-level instructions do **not** prevent leakage; TAP's anonymization
  audit (P2-03) is the right shape but runs at n=3.

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
| 16 | Execution latency class | ~ (**measured ≥11.5 min**) | ~ | ✔✔ (ns-res)¹ | n/a | ✔ (ms) | ✔ (s) | n/a | n/a | ✔ | ✔ | n/a | ✔✔ (ns–µs) |
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


¹ NautilusTrader documents nanosecond *timestamp resolution* and
live/backtest parity; no published end-to-end latency benchmark was found
(checked 10 Aug 2026) — the cell reflects resolution class, not a measured
round-trip.

**TAP column evidence (one citation per row — file, test, or artifact):**

| # | TAP cell evidence |
|---|---|
| 1–2 | `pro/dashboard/marketdata.py::default_registry` — 6 tradeable symbols (3 crypto perps, gold-via-XAUT, 2 FX) |
| 3 | P3-02 vintages: `pro/ingestion/builder.py::_apply_vintages`, `pro/store.py` vintages table |
| 4 | absence — no LOB/tick feed in `pro/ingestion/` |
| 5 | sampled only: `pro/ingestion/liquidations.py` (≤1 event/s, floor-disclaimed) |
| 6 | `pro/ingestion/gold_options.py` — GVZ IV rank/percentile, nothing else |
| 7 | `pro/ingestion/onchain.py` (CoinMetrics, FearGreed, blockchain.com) + `deribit.py` |
| 8 | `pro/ingestion/fred_macro.py` + vintage sink |
| 9 | `pro/ingestion/news.py` + 7-agent NEWS_SENTIMENT team (`pro/agents/roster.py`) |
| 10 | absence — `docs/DATA_SOURCES.md` decision table |
| 11 | `pro/pipeline/gates.py` + `pro/execution/validation.py`; `tests/test_pro_live_gates.py` |
| 12 | `pro/analytics/risk.py::portfolio_var`; `tests/test_pro_portfolio_var.py` |
| 13 | `pro/execution/safety.py::KillSwitch` (KILL file) + `CircuitBreaker`; passed drill records (`kill_switch_drill`, 8–9 Aug) |
| 14 | `pro/deadman.py`; live trips + fix `588dc81` |
| 15 | `ExecutionRouter.reconcile`; audit `reconciliation` events caught a manual venue position live (9 Aug) |
| 16 | measured: stability probe 10 Aug, ≥11.5 min/run at 8 workers |
| 17 | TWAP only: `pro/execution/router.py::_submit_twap`; `tests/test_pro_execution_twap.py` |
| 18 | `pro/service.py::_capture_tca` (arrival mid, slippage bps, markouts) |
| 19 | two adapters: `pro/execution/adapters/delta.py`, `binance_futures.py` |
| 20 | run timeline/evidence/diff endpoints (`pro/dashboard/app.py`); GateWaterfall UI |
| 21 | ADR-0015; `pro/agents/base.py` — attribution attached by code |
| 22 | `pro/execution/audit.py` hash chain + `persistence.append_line_fsync` |
| 23 | `pro/versioning.py` — git sha/prompt hash/model ids/config hash on every run and order |
| 24 | calibration/brier view models (`pro/dashboard/service.py`); `analytics/retro.py` outcome backfill |
| 25 | `pro/agents/roster.py` (59 specs) + `pro/pipeline/graph.py` |
| 26 | `pro/memory/` — analogs, lessons, model2vec embeddings (P2-04) |
| 27 | `pro/memory/graph.py` — hand-seeded (weakness #30) |
| 28 | `pro/backtest/engine.py` — decide close *i*, fill open *i+1* |
| 29 | `pro/backtest/walkforward.py`; purged K-fold in `analytics/validation.py` |
| 30 | `analytics/validation.py` — PSR, deflated Sharpe, PBO/CSCV |
| 31 | `pro/backtest/montecarlo.py` |
| 32 | backtest-only: `pro/backtest/portfolio_engine.py` + allocator; nothing live |
| 33 | `pro/rl/` — advisory-only by ADR-0025 |
| 34 | `analytics/conformal.py` — HAR-RV + ACI gate (P3-04); `tests/test_pro_conformal.py` |
| 35 | TradingView terminal: `frontend/src/lib/tv/`, mirrored library |
| 36 | `DecisionPipeline3D`, `GateWaterfall`, `DebateTimeline` components |
| 37 | `pro/alerting.py` — log/broadcast/bell/Telegram/webhook sinks |
| 38 | absence — PWA only, no push |
| 39 | `/public/v1/*` exists but ships dark (`PRO_PUBLIC_TRACK_RECORD` unset) |
| 40 | P4-03 listings + publish gate (`PRO_LISTING_MIN_GRADED`, default 30) |
| 41 | paper venue + `tests/test_pro_e2e_service.py`; 2,300+ backend tests |
| 42 | Delta testnet canary campaign armed 8–9 Aug; **0 live fills to date** |
| 43 | P3-05 viewer/operator roles; Google allowlist (`PRO_ALLOWED_EMAILS`) |
| 44 | absence — shared operator token (`docs/CONTROLS.md` §5) |
| 45 | absence |
| 46 | `/metrics` Prometheus text + `/health/live` + structured JSON logs |
| 47 | single process by construction (`deploy/Dockerfile.pro`, 1 uvicorn worker) |
| 48 | SQLite single-writer (`pro/store.py`); no worker fan-out |
| 49 | Litestream restore drill: `scripts/pro_restore_drill.sh` |
| 50 | measured Cloud Run cost ≈ ₹3.2k/mo post-optimization + LLM spend |

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

### 6. AI — 5.0/10 *(held in v2; verified against `docs/evals/`, `pro/evals/`)*
Sophisticated orchestration (59 specs, 5 teams, debate, critic
self-consistency, reflection, judge, conformal gating) and honest abstention
taxonomy (`pro/agents/base.py` — NO_DATA vs LLM_ERROR/REFUSED/UNAVAILABLE).
But: **the architecture's value is unevidenced and its own ablation is
negative** (`docs/evals/ablation.md`, n=20); the k≥30/n≥100 re-measure was
*attempted on 10 Aug and blocked by provider capacity* (see §10) — the
evidence state is unchanged, and a new defect surfaced in the measurement
tooling itself: the stability harness has no fail-fast on fatal provider
refusals (776 consecutive `LLM_REFUSED`-class errors retried for ~20 h,
despite the pipeline's own abstention taxonomy distinguishing exactly this
case). The memorization audit ran at n=3; no fine-tune, no RLVR, no
distillation; model routing is static (quick/deep), not difficulty-aware.
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

### 10. Infrastructure — 4.0/10 *(v1: 4.5 — revised down on new evidence)*
The weakest dimension and the one that bites weekly. Single uvicorn worker,
single SQLite writer, single region, no HA, no autoscale. **Live trading
currently depends on a MacBook staying awake** — the dead-man switch tripped
twice this week from host sleep (correctly), each time halting the campaign and
requiring a manual audited reset.

**New in v2 — the decision layer has no capacity redundancy either.** On
10 Aug, every configured LLM provider was simultaneously unavailable:
claude-cli (session limit — one subscription shared by the trading loop, the
eval harness, and the operator's own tooling), DeepSeek (402 insufficient
balance), OpenAI (429 no credits), Google (403), xAI (403). A trading system
whose *brain* is an external dependency held five ways with zero funded
fallbacks is a single point of failure the v1 score did not price in, because
the event had not yet been observed. The `models` health check
(`pro/health.py`) correctly detects this state — advisory, so it cannot trip
the dead-man — but detection is not redundancy. Litestream replication with a
*tested* restore drill remains the bright spot.
**Moves it:** always-on Linux host; queue + worker split; **at least one
funded metered LLM provider as a standing fallback with automatic failover**;
multi-region read replica.

### 11. Explainability — 9.5/10
The moat. Evidence carries mandatory data refs and source attribution attached
**by code, not by the model** (ADR-0015); every run is version-stamped
(git sha, prompt hash, model ids, config hash); gate waterfall shows exactly
where a decision died; run-diff explains what changed the machine's mind;
export packs are audit-ready; the UI distinguishes accepted / blocked /
rejected / no-data honestly. Nothing in the open-source or retail commercial
field matches this. Half a point withheld only because the *audit chain is
unsigned* and calibration data is still thin.

### 12. Production readiness — 6.0/10 *(held in v2; incident list grew)*
Strong drill culture (kill-switch drill passing on a real venue, restore
drill, staging→smoke→prod), real fail-closed behavior repeatedly validated in
anger. But one week of live operation produced: a silent entry halt recorded
as "accepted" (fixed in `6a1c526`), two false-positive dead-man trips from
health misclassification (fixed in `588dc81`) and host sleep (correct but
disruptive), a live venue double-fill caught only by the conformance suite
(fixed in `5140acb`), test residue polluting the live book twice (teardown
fixed in `b992997`), a Docker port collision silently swallowing API calls,
and — new in v2 — an eval harness that retried a fatal provider refusal for
~20 h without failing fast and would have emitted a fake-clean stability
number if unread. Each was found and fixed properly — but the rate says
pre-production. The score holds at 6.0 because severity and
detection-before-damage both held; the *bar to move it* explicitly has not
been met.
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

### 15. Overall — **61/100** *(v1: 62)*
Weighted: Architecture 10%, AI 15%, Quant 15%, UX 10%, Security 10%,
Infrastructure 10%, Explainability 10%, Production 10%, Institutional 5%,
Innovation 5% → 7.0(.10) + 5.0(.15) + 5.0(.15) + 8.0(.10) + 6.0(.10) +
4.0(.10) + 9.5(.10) + 6.0(.10) + 3.5(.05) + 8.0(.05) = **6.125 → 61**.
The one-point drop is entirely the Infrastructure revision: the
provider-capacity failure was observed, not hypothesized. A score that only
ever goes up under scrutiny is a score nobody should trust.

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
7. Decision latency: **measured ≥11.5 min per pipeline run** on the
   production provider at 8 workers (10 Aug); rate-limit storms on free tiers.
8. No HA, no failover, single region.
9. Operator is engineer + approver + auditor (no segregation of duties).
10. `service.py` god-object (1,337 lines) concentrates risk.
11. Free-tier-only data caps the achievable edge.
12. No L2/microstructure → no execution alpha, no informed sizing.
13. Backtest lacks execution-aware fills (no queue position, no impact).
14. Memorization audit at n=3 is not an audit.
15. Stability k≥30 / ablation n≥100 re-measure **attempted 10 Aug, blocked
    by provider capacity** — and the attempt exposed that the stability
    harness has no fail-fast on fatal provider refusals (776 retried errors,
    ~20 h, near-miss on a fake-clean result).
16. Model provider swap changes behavior with no eval gate in CI — and no
    funded fallback provider exists (all five configured providers were
    simultaneously unavailable on 10 Aug).
17. LLM cost per decision unmeasured against value produced.
18. Conformance residue polluted the live book twice *(closed: `b992997`,
    teardown flatten)*.
19. Venue coid reuse double-filled live before the session guard *(closed:
    `5140acb`)*.
20. Dead-man semantics required two fixes in one week *(closed: `588dc81` +
    advisory-check set in `pro/health.py`)*.
21. Silent entry halts recorded as "accepted" *(closed: `6a1c526` —
    `blocked:*` statuses + StatusStrip disclosure)*.
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
**Operational (7–20):** host sleep halts trading · silent halts *(closed:
`6a1c526`)* · **all configured LLM providers simultaneously unavailable
(observed 10 Aug — subscription limit + four unfunded APIs)** · venue API
changes · coid semantics differ per venue · rate limits stall decisions ·
SQLite corruption · litestream restore failure · Docker port collisions ·
test residue on live books *(closed: `b992997`)* · stale SW bundles · secret
leakage via logs · dependency compromise · unsigned audit repudiation · drift
alarm fatigue.
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

> Executable version with stable task IDs, design changes (DC-1…DC-10),
> and pre-registered gate rules: [`docs/ROADMAP_2026H2.md`](ROADMAP_2026H2.md).

### Phase 1 — Quick wins (2 weeks)
| # | Item | Value | Complexity | Effort | ROI |
|---|---|---|---|---|---|
| 1 | Move the armed loop to an always-on Linux host | Ends the #1 operational failure | Low | 1–2 d | **Very high** |
| 2 | Sign the audit chain (Ed25519 + periodic anchor) | Makes the moat legally meaningful | Low | 2–3 d | **Very high** |
| 3 | Per-person API tokens + attribution | Unblocks every compliance conversation | Low | 2 d | High |
| 4 | Auto-heal benign reconciliation drift | Stops manual-close toil, keeps the gate | Med | 2 d | High |
| 5 | Cost-per-decision metric + budget alert | Makes the AI thesis measurable in $ | Low | 1 d | High |
| 6 | Run the k≥30 stability + n≥100 ablation re-measure *(attempted 10 Aug — blocked: no funded provider; see ROI #1)* | Decides the architecture's future | Low ($10–20 of API credit) | 3 d | **Critical** |

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
**R1b (new in v2): confidence-gated debate.** [arXiv:2606.16047](https://arxiv.org/abs/2606.16047)
found full debate ≈ zero net benefit while *gated* debate (argue only the
low-confidence cases) won outright. TAP already computes per-run confidence;
gating debate on it could cut LLM cost 50–80% while — per the paper —
improving accuracy. Cheap to test on the existing eval harness.
**R1c (new in v2): heterogeneous models across teams.**
[arXiv:2502.08788](https://arxiv.org/abs/2502.08788) finds model diversity is
the one consistent fix for debate; [arXiv:2605.00914](https://arxiv.org/pdf/2605.00914)
shows same-model agents share correlated errors. TAP currently runs all 59
agents on one model; the `ModelRouting` per-team override already supports
mixing — this is a config experiment, not an engineering project.
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
~97k★), keep the *record* and the hosted Referee proprietary. **API strategy:**
public read-only track record free (credibility), write/execute paid.
**Do not chase:** HFT latency, options market-making, DRL alpha, TSFM return
prediction — all are lost rows against better-capitalized specialists.

---

## Ranked top-25 highest-ROI improvements

| # | Improvement | Impact | Effort | Risk | Measurable outcome |
|---|---|---|---|---|---|
| 1 | **Fund one metered LLM provider (~$10–20) and run the k≥30 / n≥100 re-measure** — attempted 10 Aug, blocked on capacity; ~31 h on claude-cli vs <1 h on any funded API. Add fail-fast on fatal provider refusals to the harness first (one guard clause) | **Critical** | Trivial | Low | A defensible yes/no on the core architecture; the harness can never emit a fake-clean result again |
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

**Verdict on the 75 target V1 set:** not met (v1: 62; v2: 61). It is
reachable within one quarter, but only via items 1–10 of the ROI table — not
via more features.

**v2 addendum (10 Aug):** v2 revised Infrastructure 4.5 → 4.0 after the
re-measure attempt demonstrated zero funded LLM-provider redundancy — an
event, not a hypothesis. v2 also *corrected two of v1's own citations*
(M3MAD-Bench and Voting-or-Consensus were characterized one-sidedly) and
identified that the "249/236" study is from argument mining, not finance,
and actually argues for confidence-GATED debate — which becomes R1b. An
audit that cannot audit itself would not be worth the name.
