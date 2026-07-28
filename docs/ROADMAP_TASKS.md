# Roadmap Task List (phase-wise)

Derived from `docs/COMPETITIVE_TEARDOWN.md` (§21 roadmap, §22 backlog, top-25 ROI). Each task: files to touch, acceptance criteria (AC), effort, dependencies. IDs are stable — reference them in commits (`feat(p1-03): …`).

---

## Phase 1 — Quick wins (2 weeks) · goal: prove/kill the core premise + free realism

### P1-01 pass^k decision-stability harness — 2d
- **What**: run the full pipeline k times (k=10) on one frozen snapshot; measure verdict/action/confidence flip rate.
- **Files**: new `tradingagents/pro/evals/stability.py`; CLI entry in `pro/evals/__main__.py`; reuse `pipeline_snapshot()` + real LLM bundle from `pro/models.py`.
- **AC**: report `{action_flip_rate, confidence_stddev, gate_flip_rate}` per symbol; result committed to `docs/evals/`; test with FakePipelineLLM asserts flip_rate=0 (determinism sanity).
- **Deps**: none. **Cost**: ~40 LLM runs ≈ $6.

### P1-02 Token-matched single-model ablation — 3d
- **What**: same snapshot → (a) full debate pipeline, (b) one strong model given the identical evidence pack + equal token budget, one prompt → verdict. Compare decision quality on retro-scored outcomes.
- **Files**: new `pro/evals/ablation.py`; single-model prompt built from `agents/rendering.py` evidence renderer; grade both via `analytics/retro.simulate_ticket`.
- **AC**: table over ≥20 historical snapshots: agreement %, retro-graded hit rate each, token cost each. Written to `docs/evals/ablation.md`.
- **Deps**: P1-01 harness scaffolding.

### P1-03 Perp funding costs in backtest + paper P&L — 2d
- **What**: charge realized funding on open perp positions (BTC/ETH/SOL) per bar in `backtest/broker.py` (SimBroker) and in live paper mark-to-market (`service._manage_positions`); funding series from Delta/Binance feeds (already ingested as FUNDING_RATE — persist history).
- **Files**: `pro/backtest/broker.py`, `pro/service.py`, `ingestion/delta_exchange.py` (funding history endpoint), tests in `tests/test_pro_backtest*.py`.
- **AC**: backtest report shows `funding_paid`; journal outcomes include funding in pnl; test: known funding series → exact expected charge.
- **Deps**: none.

### P1-04 TCA capture on paper fills — 2d
- **What**: on every fill record arrival mid, fill price, and mid at +30s/+1m/+5m (tick cache lookups); persist on the outcome payload; slippage-drift view.
- **Files**: `pro/execution/venues.py` (fill record), `pro/service.py` (markout scheduler — one `threading.Timer` chain is fine), `dashboard/service.py` (add `tca` block to journal entries), small Portfolio UI row.
- **AC**: journal CSV gains arrival/markout columns; Portfolio shows avg realized slippage vs the 3bps assumption; test with scripted ticks.
- **Deps**: none. `ponytail:` timers lost on restart — acceptable, markouts are best-effort.

### P1-05 Free-data sprint (4 adapters) — 4d
- **What**: (a) Deribit options IV/term structure (public API) → `DVOL`/IV metrics; (b) Coin Metrics Community (no key) → realized cap, active addresses; (c) World Gold Council Goldhub → ETF flows + central-bank purchases (monthly CSV); (d) Token Terminal free tier (optional, lowest priority).
- **Files**: new `ingestion/deribit.py`, `ingestion/coinmetrics_community.py` (extend `onchain.py`), `ingestion/goldhub.py`; register in `dashboard/intel.py` feeds + `METRIC_INFO`; wire into crypto/gold snapshot builders (`pro/main.py`); remove satisfied entries from `unsubscribed_feeds`.
- **AC**: new tiles on Intel with labels/notes; metrics reach agent prompts (rendering test); each adapter has a transport-stubbed unit test.
- **Deps**: none.

### P1-06 Feed-age trading halt + alert-state persistence — 1d
- **What**: (a) gate: if driving bars' last timestamp older than 2× timeframe → block entries (`blocked:stale_data`) + alert; (b) persist `_intel_state` dict to prefs so condition alerts don't re-fire on restart.
- **Files**: `pro/service.py` (entry path check + state load/save via `dashboard/prefs.py`).
- **AC**: e2e test: stale snapshot → `blocked:stale_data`; restart test: state survives, no duplicate alert.
- **Deps**: none.

### P1-07 Slim prod image + SBOM + pip-audit — 1d
- **What**: `.dockerignore` (tests/, docs/, .git, frontend/node_modules, *.md except needed); move `FakePipelineLLM` dependency already in package (done) so tests/ can drop; add `pip-audit` + `npm audit --audit-level=high` steps to `pro-ci.yml`; emit SBOM (`pip freeze` artifact suffices).
- **Files**: new `.dockerignore`, `.github/workflows/pro-ci.yml`, `deploy/Dockerfile.pro` sanity.
- **AC**: image builds, demo boots, replay endpoint still works (it imports from the package now); CI red on known-CVE dep.
- **Deps**: none. ⚠ verify `scripts/pro_dashboard_demo.py` (imports tests/) is not used by the prod entrypoint.

### P1-08 Brier score + demote verbalized confidence — 1d
- **What**: compute Brier/log-loss of stated confidence vs outcomes in `agent_performance`/calibration view; show next to calibration chart; UI: p(win) becomes the primary number when n≥5, stated confidence moves to secondary text.
- **Files**: `dashboard/service.py`, `frontend/src/components/CalibrationChart.tsx`, `DecisionCard.tsx`.
- **AC**: Brier displayed with n; ticket shows p(win) prominent when available; unit test for Brier math.
- **Deps**: none (retro corpus exists).

---

## Phase 2 — Professional-grade (2 months) · goal: kill the infrastructure/quant ceilings

### P2-01 Event-store migration — 2w
- **What**: SQLite (WAL) + litestream→GCS as the single source of truth for runs/memory/outcomes/orders; JSONL becomes export format. Append-only tables + views; recorder/memory/prefs write through one store module.
- **Files**: new `pro/store.py`; refactor `dashboard/recorder.py`, `memory/memory.py` (JsonlStore swap), `dashboard/prefs.py`; migration script reading existing JSONL.
- **AC**: restore drill documented + tested (delete container, replay from GCS, state identical); all 1,331 tests green; `--max-instances` comment updated (constraint now advisory).
- **Deps**: none. Blocks P2-08, P3-05.

### P2-02 Purged CV + Deflated Sharpe gate — 1w
- **What**: purged/embargoed K-fold splitter over bar history; DSR + PBO computed for any strategy config; backtest UI shows them; a config with DSR below threshold is labeled "not deployable".
- **Files**: new `pro/analytics/validation.py` (López de Prado formulas — pure numpy); `backtest/engine.py` hook; `dashboard/app.py` backtest endpoint params; Portfolio UI badges.
- **AC**: unit tests vs published DSR examples; backtest response includes `{dsr, pbo, n_trials}`.
- **Deps**: P1-03 (costs first, or DSR gates a fantasy).

### P2-03 Published eval protocol + memorization audit — 1w
- **What**: (a) eval doc: post-cutoff-only windows, cost-inclusive, all symbols, pre-registered; (b) anonymizer: re-run stored snapshots with tickers/dates masked ("ASSET_A", relative dates) through the pipeline; compare verdicts.
- **Files**: new `pro/evals/anonymize.py`, `docs/EVAL_PROTOCOL.md`; snapshot masking at `agents/rendering.py` boundary (flag).
- **AC**: report: agreement rate named-vs-anonymized per symbol; protocol doc published; anonymizer covered by test.
- **Deps**: P1-01/P1-02 harness.

### P2-04 Semantic embeddings for memory — 3d
- **What**: replace `HashingEmbedder` default with a small local model (e.g. already-installed provider embedding endpoint, else `sentence-transformers` MiniLM) behind the existing `EmbeddingFn` protocol; re-embed store on migration.
- **Files**: `memory/embedding.py`, config flag in `pro/main.py`.
- **AC**: analog quality spot-check (10 queries human-rated better/equal/worse vs hashing — commit results); latency <100ms/query; fallback to hashing if model unavailable.
- **Deps**: none.

### P2-05 Portfolio-level VaR + correlation-aware caps — 1w
- **What**: portfolio VaR from position weights × return covariance (reuse `intel.correlation_matrix` inputs); pre-trade check: new position may not push portfolio VaR or pairwise-correlated gross beyond limits.
- **Files**: `pro/analytics/risk.py` (portfolio_var), `execution/validation.py` (new check), `dashboard/service.py` exposure view, StatusStrip chip.
- **AC**: test: adding correlated ETH to BTC book trips the cap where uncorrelated gold doesn't; UI shows book VaR.
- **Deps**: none.

### P2-06 Event-driven triggers — 1w
- **What**: besides hourly rotation: trigger a run when (a) a major calendar event just released (T+5min), (b) realized vol spikes (threshold on last bar), (c) price gaps >n×ATR. Debounce per symbol; respect run_lock + daily order cap.
- **Files**: `pro/service.py` (trigger evaluation in a light 60s loop or piggyback tick poller), config in `contracts/config.py`.
- **AC**: e2e test with scripted calendar → run fires once; loop still serialized; alert emitted "event-triggered run".
- **Deps**: P1-06 (stale gate) recommended first.

### P2-07 Custom alert-builder UI — 1w
- **What**: prefs-backed `condition_alerts` CRUD (mirror `price_alerts` in `dashboard/prefs.py` + endpoints) with metric/operator/threshold from `METRIC_INFO` keys; UI on Intel page; engine already evaluates defaults — generalize `_evaluate_intel_alerts` to iterate stored conditions.
- **Files**: `dashboard/prefs.py`, `dashboard/app.py`, `pro/service.py`, new `frontend/src/components/AlertBuilder.tsx`.
- **AC**: create/delete via UI; crossing fires bell+Telegram; persisted across restart (P1-06).
- **Deps**: P1-06.

### P2-08 Staging env + external metrics — 3d
- **What**: second Cloud Run service `pro-dashboard-staging` (separate bucket, PRO_LOOP_DISABLED=1) deployed first by the script; export `MetricsRegistry` to Cloud Monitoring (or `/metrics` Prometheus scrape via sidecar-less OpenTelemetry).
- **Files**: `scripts/deploy_cloud_run.sh` (staging step + promote), `pro/observability.py` (exporter).
- **AC**: deploy flow = staging→smoke→prod; dashboards show run counts/errors/latency externally.
- **Deps**: none (better after P2-01).

### P2-09 Evidence-chip → chart plotting — 4d
- **What**: level-bearing data refs (order_block, sma, liquidity levels) clickable on Decisions → navigate to Trade with the level drawn (reuse the AI-ticket price-line path in `PriceChart.tsx`).
- **Files**: `frontend/src/components/EvidencePanel.tsx`, `PriceChart.tsx`, a `levelFromRef()` parser in `frontend/src/lib/`.
- **AC**: click `order_block` chip → chart opens with the zone plotted + labeled; refs without numeric levels render non-clickable.
- **Deps**: none.

### P2-10 FX majors (EURUSD, USDJPY) — 2w
- **What**: generalize `OandaGoldFeed` → `OandaFeed(instrument)`; AssetClass.FOREX + DEFAULT_SYMBOLS; registry entries (live if OANDA token, else yfinance D1); venue map (`paper` + `oanda`); FX-relevant macro agents already exist (rates/DXY); loop rotation grows to 6.
- **Files**: `ingestion/oanda_gold.py`→rename, `contracts/enums.py`, `dashboard/marketdata.py`, `execution/venues.py`, `pro/main.py` wiring map, roster check for FX metric coverage.
- **AC**: EURUSD run end-to-end on paper; charts live; tests for enum ripple.
- **Deps**: P2-01 helpful (more writers), not required.

### P2-11 Liquidation reconstruction — 1w
- **What**: Binance futures `forceOrder` WS (sampled) + OI deltas → per-symbol liquidation intensity metric + price-bucket heatmap (signal-grade, magnitudes disclaimed).
- **Files**: new `ingestion/liquidations.py` (WS thread like `QuoteTickPoller`), intel registration, Intel UI tile/heat strip.
- **AC**: metric flows to intel + condition alerts; disclaimer note in METRIC_INFO; unit test with recorded frames.
- **Deps**: P1-06 persistence pattern.

---

## Phase 3 — Institutional-grade (6 months)

### P3-01 Live dust pilot (one exchange) — 3w
- **What**: real orders at minimum size on one venue (Delta or Binance testnet→mainnet dust), reduce-only ladder to start; real TCA (P1-04 pipeline against real fills); protective stop placed ON VENUE at entry (fixes LLM-outage risk #48).
- **Files**: `execution/` live adapter (extend existing live-gates/OMS scaffolding — `live_config.py`, `arming.py` exist), runbook doc.
- **AC**: 10 real fills recorded with TCA; kill-switch drill executed against live venue; arming ceremony documented.
- **Deps**: P1-03, P1-04, P2-05. ⚠ real money — owner sign-off gate.

### P3-02 PIT vintage store — 2w
- **What**: store every metric observation as `(name, value, observed_at, as_of)`; FRED ALFRED endpoint for vintages; snapshots read "as known at decision time"; retro-scorer + backtests read vintages.
- **Files**: `pro/store.py` (P2-01 table), `ingestion/fred_macro.py` (ALFRED), `ingestion/builder.py` read path.
- **AC**: test: revised NFP does not change a past decision's recorded inputs; backfill keeps both series.
- **Deps**: P2-01.

### P3-03 LLM factor-mining loop (RD-Agent pattern) — 3w
- **What**: LLM proposes formulaic factors over our bar/metric history → evaluated OOS (purged CV, IC/decay) → surviving factors become evidence agents with computed values (not opinions).
- **Files**: new `pro/analytics/factors.py` (safe expression evaluator over pandas — no exec), `pro/evals/factor_mining.py` loop, roster registration for survivors.
- **AC**: ≥1 factor with OOS IC beating an Alpha158-style baseline on our symbols, or a documented negative result; factor values render as evidence with data refs.
- **Deps**: P2-02.

### P3-04 Conformal vol intervals → gates — 2w
- **What**: adaptive conformal prediction on realized-vol forecasts (HAR baseline); gate consumes interval width (block entries when interval blows out; size down proportionally).
- **Files**: `pro/analytics/conformal.py`, `pipeline/nodes.py` risk-gate input, config thresholds.
- **AC**: empirical coverage ≈ nominal on holdout; gate behavior test.
- **Deps**: P2-02 splitters.

### P3-05 Multi-tenant auth + entitlements — 3w
- **What**: user table (P2-01 store), roles (viewer/operator), per-user prefs/watchlists; operator-only mutation endpoints; session middleware extension.
- **Files**: `dashboard/app.py` middleware, `pro/store.py`, prefs refactor.
- **AC**: two users, isolated prefs, viewer cannot trigger runs/flatten; auth tests.
- **Deps**: P2-01.

### P3-06 Decision-audit export packs — 3d
- **What**: per-run PDF/JSON bundle: snapshot inputs (as-of), full transcript, gates, order, fills, outcome, calibration context, model/prompt versions. The compliance artifact.
- **Files**: `dashboard/app.py` export endpoint (reuse report/PDF path), version stamping from P3-07.
- **AC**: one click → complete pack for any run; golden-file test.
- **Deps**: P3-07 tagging.

### P3-07 Algo/version tagging — 2d
- **What**: stamp every run/order with `{git_sha, prompt_hash, model_ids, config_hash}`.
- **Files**: `pro/service.py`, `dashboard/recorder.py`, order payloads in `execution/`.
- **AC**: `/api/runs` rows carry versions; changing a prompt changes the hash (test).
- **Deps**: none (do early in Phase 3).

### P3-08 Quarterly self-assessment generator — 2d
- **What**: RTS-6-flavored markdown: algos run, limits fired, kill-switch tests, incidents, changes — generated from the event store.
- **Files**: `pro/evals/self_assessment.py` or dashboard endpoint.
- **AC**: `make self-assessment` emits the doc for a date range.
- **Deps**: P2-01, P3-07.

### P3-09 Gold options analytics — 2w
- **What**: Deribit + (free) CME settlement-based IV context for gold; vol-surface tile; IV-rank condition alert; evidence agent.
- **Deps**: P1-05 Deribit adapter.

### P3-10 TWAP order slicing — 1w
- **What**: split entries into n child orders over m minutes on the paper/live path; TCA compares sliced vs single.
- **Files**: `execution/router.py`/OMS.
- **Deps**: P3-01.

### P3-11 Public read-only API + webhooks — 1w
- **What**: token-scoped read endpoints (decisions, calibration) + webhook on run_complete; rate limiting (fixes weakness #29).
- **Deps**: P3-05.

### P3-12 SOC2-track controls doc — ongoing
- **What**: access control, change management, backup/restore evidence, incident log — written against what exists.
- **Deps**: P2-08, P3-08.

---

## Phase 4 — World-class (12 months)

| ID | Task | Effort | Deps |
|---|---|---|---|
| P4-01 | RLVR fine-tune on own graded-outcome corpus (Trading-R1 recipe); A/B vs prompted pipeline via P1-02 harness | 6w | ≥200 graded outcomes, P2-03 |
| P4-02 | Public live track-record page (pre-registered decisions, graded post-hoc; contamination-proof) | 1w + legal | P3-06, counsel |
| P4-03 | Calibration-gated strategy/prompt marketplace (listings must publish graded records) | 8w | P3-05, P3-11 |
| P4-04 | Equities via Alpaca paper (corporate actions handling!) | 4w | P2-10 patterns |
| P4-05 | Portfolio optimizer across book (mean-variance/HRP over decisions) | 3w | P2-05 |
| P4-06 | Mobile push (FCM on PWA) + position widgets | 2w | P3-05 |
| P4-07 | White-label/on-prem deploy kit | 3w | P2-01, P2-08 |
| P4-08 | Universe to 20+ symbols; scanner-ranked LLM budget allocation (top-k get full runs) | 3w | P2-10 |
| P4-09 | Independent security audit + pen test | external | P3-05, P3-11 |

## Phase 5 — 10× bets (sequenced after Phase 3 exit)

| ID | Bet | First milestone |
|---|---|---|
| P5-01 | **The Referee** — hosted contamination-proof benchmark for ANY AI trading system (pre-registered decisions, we grade) | our own pipeline as first tenant on the public page (P4-02) |
| P5-02 | Audit-trail-as-a-service — decision-provenance API for other AI trading products | one design partner using P3-06 packs |
| P5-03 | Calibration-priced subscription tier | pricing experiment behind a flag |
| P5-04 | Adversarial market twin (crowding detection vs simulated LLM-trader population) | correlation study vs public LLM baselines (research backlog #9) |
| P5-05 | "What changed the machine's mind" daily institutional brief (temporal explainability diff) | run-diff view (missing-feature #79) as the primitive |

---

## Sequencing notes
- **Gate everything on P1-01/P1-02 results.** If the ablation shows single-model parity, Phases 2–5 proceed unchanged but the pipeline gets cheaper (debate kept for auditability only, one strong model decides); if debate wins, publish it — first evidence in the field.
- Phase 1 is entirely parallelizable; P2-01 (event store) is the long pole of Phase 2 — start it week 1 of the phase.
- Hard external gates: P3-01 (owner sign-off, real funds), P4-02/P5-01 (legal counsel).
