# TradingAgents Pro — Architecture, Data Flow & Component Diagrams

> Regenerates the HLD/LLD diagrams lost to the iCloud incident (ADR-0013).
> Narrative history lives in [`ARCHITECTURE.md`](../../ARCHITECTURE.md); design
> decisions in [`DECISIONS.md`](../../DECISIONS.md); hands-on instructions in
> [`docs/DEVELOPER_GUIDE.md`](../DEVELOPER_GUIDE.md). Diagrams are Mermaid and
> render on GitHub.

---

## 1. System architecture

Two deployment shapes run the **same codebase** (`tradingagents.pro.main`):
the always-on **cloud paper deployment** (Cloud Run) and the **local armed
host** used for the P3-01 live dust pilot. Live order routing only exists
where an operator has run the arming ceremony — the cloud service never
holds venue trading keys.

```mermaid
flowchart TB
    subgraph Client["Operator's browser"]
        SPA["React SPA<br/>(Vite build, PWA)"]
        TV["TradingView widget<br/>(same-origin iframe,<br/>mirrored /charting_library/)"]
        SPA --- TV
    end

    subgraph GCP["Google Cloud"]
        FB["Firebase Hosting<br/>rewrite ** → Cloud Run"]
        subgraph CR["Cloud Run: pro-dashboard (PAPER)"]
            direction TB
            LS["Litestream<br/>(entrypoint -exec wrapper)"]
            APP1["uvicorn ×1 worker<br/>FastAPI dashboard + hourly paper loop<br/>(single process — SSE broadcaster,<br/>session store, SQLite writer)"]
            LS --> APP1
        end
        STG["Cloud Run: pro-dashboard-staging<br/>(scale-to-zero, loop disabled,<br/>deploy smoke-gate only)"]
        GCS[("GCS buckets<br/>/data volume + litestream replica")]
        SM["Secret Manager<br/>(LLM keys, dashboard token,<br/>FRED, Telegram)"]
        FB --> APP1
        APP1 <--> GCS
        SM --> APP1
    end

    subgraph Local["Local armed host (P3-01 dust pilot)"]
        LOOP["python -m tradingagents.pro.main<br/>under caffeinate, PORT 8600<br/>live.yaml + arming.json + KILL file"]
        ENV[".env (0600, operator-typed)<br/>DELTA_TESTNET keys, PRO_LIVE_CONFIG"]
        ENV --> LOOP
    end

    subgraph Vendors["Market data vendors (free-first)"]
        PYTH["Pyth UDF history<br/>thefundedroom.com/api/pyth/history"]
        HERMES["Hermes price stream<br/>wss://datafeeds-590z.onrender.com"]
        DELTA["Delta Exchange India<br/>testnet + production"]
        OTHERS["Binance · OANDA · FRED ·<br/>CoinMetrics · Deribit · Yahoo ·<br/>alternative.me · CFTC · Goldhub CSV"]
    end

    subgraph LLM["LLM providers"]
        PROV["groq / deepseek / openai / xai / google …<br/>(OpenAI-compatible registry)"]
        CLI["claude-cli<br/>(subscription via local login or<br/>CLAUDE_CODE_OAUTH_TOKEN headless)"]
    end

    subgraph Alerts["Alert sinks"]
        TG["Telegram"]
        WH["Webhooks (HMAC X-Pro-Signature)"]
    end

    SPA -->|"HTTPS /api/* (X-API-Key + cookie)"| FB
    SPA -->|"SSE (ticketed direct or same-origin)"| APP1
    SPA -->|"WSS ticks (browser-direct, no CORS on WS)"| HERMES
    APP1 -->|"/api/tv/history proxy (TTL cache)"| PYTH
    APP1 -->|read-only market data| OTHERS
    APP1 --> PROV & CLI
    APP1 --> TG & WH
    LOOP -->|"signed REST (token bucket)"| DELTA
    LOOP --> PROV & CLI
    LOOP -->|read-only market data| OTHERS
    SPA -.->|"operator drives localhost:8600<br/>when campaign runs locally"| LOOP
```

**Load-bearing constraints**

| Constraint | Why | Enforced at |
|---|---|---|
| Single uvicorn worker | in-process SSE broadcaster, session store, and the SQLite single-writer all live in one process | `deploy/Dockerfile.pro` CMD |
| SQLite on local disk, never GCS FUSE | FUSE cannot hold SQLite locks; durability comes from Litestream replication instead | `store.default_db_path()` → `/tmp/pro.db` in the image |
| Live keys only on the armed host | cloud is paper-only; `arm-live` is an interactive CLI ceremony with a typed confirmation phrase | `pro/cli.py`, `pro/arming.py` |
| Staging always loop-disabled | one accrual clock; no duplicate paper orders | `scripts/deploy_cloud_run.sh` |
| TradingView library mirrored same-origin | the widget iframe cannot be scripted cross-origin | `scripts/mirror_charting_library.py` → `frontend/public/charting_library/` |

---

## 2. Data-flow diagrams

### 2.1 Decision pipeline (one run)

A run is a LangGraph execution over `PipelineState`. Every rejection path is
an honest outcome; LLMs never override deterministic gates.

```mermaid
flowchart TB
    subgraph Ingest["SnapshotBuilder (pro/ingestion/builder.py)"]
        FEEDS["bars + quote + macro + onchain + news feeds<br/>(each failure → missing_feeds, never fabricated;<br/>bar failure aborts the build — no price, no snapshot)"]
        SNAP["MarketSnapshot<br/>bars/indicators/macro/onchain/news/<br/>session/missing_feeds"]
        FEEDS --> SNAP
    end

    SNAP --> PREP

    subgraph Graph["LangGraph pipeline (pro/pipeline/graph.py)"]
        PREP["prepare<br/>quant + neutral risk metrics, regime,<br/>RL advisory, memory analogs/lessons<br/>(wrap_untrusted), EVENT GATE first<br/>— before any LLM spend"]
        PREP -->|event gate fail| REJ

        subgraph Teams["5 parallel evidence teams (59 AgentSpecs)"]
            T1["team_technical (24)"]
            T2["team_macro (13)"]
            T3["team_news_sentiment (7)"]
            T4["team_quant (8 + mined factors P3-03)"]
            T5["team_risk (9)"]
        end
        PREP --> T1 & T2 & T3 & T4 & T5
        T1 & T2 & T3 & T4 & T5 --> JOIN

        JOIN["join<br/>no evidence anywhere → rejected<br/>(cause-aware: provider outage ≠ silence)"]
        JOIN -->|empty| REJ
        JOIN --> DB1["technical_bull ⇄ technical_bear<br/>(max_debate_rounds)"]
        DB1 --> DB2["macro_bull ⇄ macro_bear"]
        DB2 --> SENT["sentiment rapporteur"]
        SENT --> RG["risk_gate (deterministic)<br/>+ conformal vol gate P3-04"]
        RG -->|fail| REJ
        RG --> CRITIC["critic (deep model)<br/>self-consistency: N samples,<br/>majority rule, ties fail closed"]
        CRITIC -->|fail| REJ
        CRITIC --> REFL["reflection<br/>invalidation_price → stop"]
        REFL --> JUDGE["judge (deep model)<br/>sees computed consensus;<br/>rules mode = consensus + ADX filter"]
        JUDGE --> PM["portfolio_manager<br/>sided risk metrics, sizing,<br/>vol_interval_scale, re-run risk_gate,<br/>trade_quality_gate → TradeRecommendation"]
        PM -->|fail| REJ
        PM --> HA{"human_approval<br/>(LIVE only: interrupt)"}
        HA -->|decline| REJ
        HA --> EXEC["execution<br/>(status only — routing happens<br/>in the service, not the graph)"]
        REJ["rejected<br/>execution_status = rejected:&lt;stage&gt;"]
    end

    EXEC & REJ --> REC["PipelineRecorder → RunRecord<br/>(node_sequence, node_times, gates,<br/>debate, snapshot, version stamp P3-07)"]
    REC --> STORE[("EventStore (SQLite WAL)")]
    REC --> SSE["SSE run/status events → dashboard"]
```

Abstention taxonomy (per agent): `NO_DATA` (honest coverage gap) vs
`LLM_ERROR` / `LLM_REFUSED` / `LLM_UNAVAILABLE` (model layer broken) — the
join node words its rejection differently for each, so a billing outage never
masquerades as "no signal".

### 2.2 Live order routing & the safety stack

```mermaid
flowchart TB
    RECO["TradeRecommendation<br/>(accepted run)"] --> TRADEMODE{"arming tier for symbol?<br/>(arming.json, TTL-expiring)"}
    TRADEMODE -->|paper| PAPER["paper venue<br/>(simulated fills, same OMS semantics)"]
    TRADEMODE -->|shadow| SHADOW["shadow: live prices,<br/>no orders, ShadowFillTracker"]
    TRADEMODE -->|canary / live| ROUTER

    subgraph ROUTER["ExecutionRouter (pro/execution/router.py)"]
        R0["kill switch engaged? → refuse"]
        R1["canary: clamp to venue minimum<br/>BEFORE validation (gates judge the<br/>order that will actually be placed)"]
        R2["LiveRiskLimits validation:<br/>notional cap (equity% × leverage),<br/>order rate caps, spread/cross caps,<br/>portfolio VaR + correlated gross P2-05"]
        R3["TWAP slicing P3-10<br/>(deterministic #twapK coids)"]
        R0 --> R1 --> R2 --> R3
    end

    ROUTER -->|refusal| AUDIT
    ROUTER --> OMS

    subgraph OMS["OrderManager (pro/execution/oms.py)"]
        O1["deterministic client_order_ids"]
        O2["entry + bracket ATOMIC:<br/>reduce-only stop rests ON THE VENUE"]
        O3["resolve loop: transport failure →<br/>get_order(coid) before any resubmit"]
        O1 --> O2 --> O3
    end

    OMS --> ADAPTER

    subgraph ADAPTER["DeltaAdapter (adapters/delta.py)"]
        A1["session idempotency guard:<br/>venue only dedupes OPEN orders —<br/>a sent coid is never resubmitted"]
        A2["client-side token bucket<br/>(10k units / 5 min)"]
        A3["HMAC signing, 5s validity;<br/>signature rejection = the authoritative<br/>clock check (Date header is CDN edge)"]
        A1 --> A2 --> A3
    end

    ADAPTER -->|signed REST| VENUE[("Delta India<br/>testnet / production")]

    subgraph Watchers["Independent safety layers"]
        W1["BracketWatchdog:<br/>stop can't be placed → flatten"]
        W2["CircuitBreaker:<br/>consecutive losses / daily loss<br/>→ halt new entries"]
        W3["Dead-man switch:<br/>execution_ok heartbeat unconfirmed<br/>600s → cancel resting orders +<br/>engage kill switch (advisory checks<br/>feeds/models never trip it)"]
        W4["Reconciliation loop:<br/>local book vs venue —<br/>unknown_on_venue disclosed, in_sync flag"]
        W5["KillSwitch: in-memory + KILL file;<br/>reset is an explicit audited<br/>operator action, never automatic"]
        W6["emergency_flatten (CLI + API):<br/>cancel all → close all → disarm all"]
    end

    VENUE -. positions/orders .-> Watchers
    Watchers --> AUDIT[("audit.jsonl<br/>hash-chained, fsync append<br/>+ P3-07 version stamps")]
    OMS --> AUDIT
    ROUTER --> AUDIT
```

### 2.3 Market data & charts

```mermaid
flowchart LR
    subgraph Browser
        TVW["TradingView widget"]
        DF["lib/tv/datafeed.ts<br/>resolveSymbol/getBars/<br/>subscribeBars/getMarks"]
        FAV["FavoritesBar<br/>(live mids, stale dimming)"]
        STREAM["shared PythStream<br/>(one WS, per-pair fan-out,<br/>30s silence watchdog, backoff)"]
        TVW --> DF
        DF --> STREAM
        FAV --> STREAM
    end

    subgraph Backend["FastAPI dashboard"]
        TVH["/api/tv/history<br/>symbol→Pyth map, 15s TTL cache,<br/>keep-alive Session,<br/>PRO_TV_HISTORY_BASE=synthetic for CI"]
        BARS["/api/bars → MarketDataService<br/>(probe-gated registry, TTL cache,<br/>single-flight, Pyth price overlay<br/>+ venue volume for crypto)"]
        ANNOT["/api/chart/annotations<br/>runs+fills joined, 'inferred' links"]
    end

    STREAM -->|wss| HERMES[("Hermes stream<br/>(Pyth/Hermes mids)")]
    DF -->|auth fetch| TVH
    DF -->|marks| ANNOT
    TVH --> PYTH[("Pyth UDF history API")]
    BARS --> VENUES[("Delta / Binance /<br/>OANDA / yfinance")]
    SPARK["Home PriceSpark,<br/>correlation, backtests"] --> BARS
```

### 2.4 Events, alerts & UI state

```mermaid
flowchart LR
    WORKER["trading worker thread<br/>(loop / on-demand run)"] -->|"publish (thread-safe<br/>call_soon_threadsafe)"| BC["EventBroadcaster<br/>replay ring + per-client<br/>bounded queues"]
    BC -->|"SSE: run/position/status/<br/>tick/alert/stage"| CLIENTS["browser EventSource<br/>same-origin cookie OR<br/>single-use ticket direct<br/>to Cloud Run (buffering proxy)"]
    CLIENTS --> TQ["TanStack Query invalidation<br/>(5s poll fallback relaxes to 60s<br/>while SSE healthy)"]

    WORKER --> AM["AlertManager"]
    AM --> S1["LogAlertSink"]
    AM --> S2["BroadcastAlertSink → SSE"]
    AM --> S3["NotificationSink → bell<br/>(PrefsStore, persisted)"]
    AM --> S4["TelegramAlertSink"]
    AM --> S5["WebhookAlertSink (HMAC)"]

    TICKER["QuoteTickPoller + TickCache"] --> BC
    TICKER --> PAE["PriceAlertEngine<br/>(notify-only, G4)"] --> AM
```

### 2.5 Persistence

```mermaid
flowchart TB
    subgraph Writers["Single-writer adapters"]
        RECR["PipelineRecorder<br/>(RunRecord JSON)"]
        MEM["ProMemory<br/>(append-only records:<br/>TRADE→OUTCOME→LESSON)"]
        PREFS["PrefsStore<br/>(watchlists, alerts, bell,<br/>layouts mirror)"]
        USERS["users / api_tokens (P3-05)"]
        LIST["listings (P4-03)"]
        VINT["FRED vintages (P3-02<br/>point-in-time replay)"]
    end

    Writers --> ES[("EventStore — pro/store.py<br/>ONE SQLite DB, WAL mode,<br/>single writer connection + lock.<br/>MUST be local disk")]
    ES -->|"litestream replicate<br/>(entrypoint exec wrapper)"| GCS[("GCS replica<br/>restore drill:<br/>scripts/pro_restore_drill.sh")]

    subgraph Files["File-backed state (outside SQLite)"]
        AUD["audit.jsonl — hash-chained,<br/>append_line_fsync"]
        ARM["arming.json — atomic_write_json"]
        KILL["KILL file — kill switch latch"]
        BAK["backtest_runs/&lt;id&gt;/ artifacts<br/>(checkpoint every 25 decisions)"]
    end
```

---

## 3. Component diagram & responsibilities

```mermaid
flowchart TB
    subgraph Foundation
        CONTRACTS["tradingagents/contracts<br/>typed spine (frozen pydantic)"]
        LLMC["tradingagents/llm_clients<br/>provider factory + structured output"]
    end

    subgraph Data
        INGEST["pro/ingestion<br/>feeds → MarketSnapshot"]
    end

    subgraph Brain
        AGENTS["pro/agents<br/>59 evidence specs (config)"]
        PIPE["pro/pipeline<br/>LangGraph debate + gates"]
        MEMORY["pro/memory<br/>analogs, lessons, embeddings"]
        ANALYT["pro/analytics<br/>risk math, conformal, regimes,<br/>factors, validation stats"]
        RL["pro/rl (advisory only)"]
    end

    subgraph Money
        EXEC["pro/execution<br/>router, OMS, adapters, safety"]
        ARMING["pro/arming + live_config<br/>tiers, ceremonies, TTL"]
    end

    subgraph Serve
        SERVICE["pro/service.py<br/>the loop: snapshot→run→route→manage"]
        DASH["pro/dashboard<br/>FastAPI + view models + SSE"]
        FE["frontend/ React SPA"]
    end

    subgraph Verify
        BT["pro/backtest (36 modules)"]
        EVALS["pro/evals"]
        TESTS["tests/ + frontend/e2e"]
    end

    OPS["deploy/ + scripts/<br/>Docker, Cloud Run, drills"]

    CONTRACTS --> INGEST & AGENTS & PIPE & EXEC & SERVICE & BT
    LLMC --> PIPE & AGENTS & EVALS
    INGEST --> SERVICE & BT
    AGENTS --> PIPE
    MEMORY --> PIPE
    ANALYT --> PIPE & EXEC & BT
    RL -.advisory metrics.-> PIPE
    PIPE --> SERVICE
    EXEC --> SERVICE
    ARMING --> EXEC
    SERVICE --> DASH
    DASH --> FE
    EVALS -.replay engine.-> DASH
```

### 3.1 Foundation

| Component | Responsibility | Key symbols | Depends on → consumed by |
|---|---|---|---|
| `tradingagents/contracts/` | The typed spine every layer exchanges: frozen Pydantic v2 models, `extra="forbid"`, UTC-only. Live trading is structurally impossible unless `live_trading_enabled` AND `require_human_approval`. Side-aware trade geometry is validated at construction (BUY: stop < entry < TP ladder). | `MarketSnapshot`, `OHLCVBar`, `AgentEvidence` (≥1 data ref + attributed source), `TradeRecommendation`, `ProConfig`, `RiskLimits`, `LiveRiskLimits`, `Timeframe`, `AssetClass` | stdlib/pydantic → everything |
| `tradingagents/llm_clients/` | One factory over every provider; the universal seam is `with_structured_output(schema).invoke(prompt)` — anything satisfying it is substitutable (fakes, rules engine, anonymizer). `capabilities.py` is the single table of per-model quirks. | `create_llm_client`, `OpenAIClient` + `ProviderSpec` registry, `ClaudeCLIChat` (shells `claude -p`, JSON repair, env scrubbing), `get_capabilities`, `api_key_env.py` | contracts → pipeline, agents, evals |
| `pro/models.py` + `pro/observability.py` | Model routing (teams→quick, critic/reflection/judge→deep), pinned-model enforcement, cost/metrics wrapper. `llm_calls_total` counts successes only; failures land in `llm_failures_total` — the models health check reads both. | `ModelBundle.coerce`, `is_pinned_model`, `CostTrackingLLM`, `MetricsRegistry` (Prometheus text), `JsonFormatter` | llm_clients → service, health |

### 3.2 Data layer

| Component | Responsibility | Key symbols | Depends on → consumed by |
|---|---|---|---|
| `pro/ingestion/builder.py` | The single place feed failure is absorbed. Bars are load-bearing (failure aborts the build); everything else degrades into `missing_feeds` — disclosed, never fabricated. P3-02 vintage replay for as-of builds. | `SnapshotBuilder.build`, `build_gold_pipeline`, `build_bitcoin_pipeline` | feeds → service, backtest |
| Feed adapters (`binance` `delta_exchange` `oanda_gold` `gold_feeds` `gold_options` `positioning` `goldhub` `fred_macro` `econ_calendar` `onchain` `deribit` `liquidations` `news` `token_terminal` `pyth` `sessions`) | Typed contracts behind three Protocols (`BarsFeed`/`QuoteFeed`/`MetricsFeed`) with injectable transports; free-first per `docs/DATA_SOURCES.md`. `liquidations` is explicitly signal-grade (sampled; notionals are a floor). `pyth` gives one price series across asset classes with venue volume overlaid. | `RequestsTransport` (429→`VendorRateLimitError`), `LiquidationStream.shared_stream`, `PythPriceVenueVolumeFeed`, `PYTH_SYMBOLS` | base.py → builder, marketdata, intel |
| `pro/ingestion/indicators.py` | Deterministic indicator engine (stockstats + hand-rolled VWAP/OBV) with warm-up discipline. The UI renders these, never computes its own. | `compute_indicators`, `INDICATOR_SPECS` | bars → snapshots, `/api/bars/indicators` |

### 3.3 Decision brain

| Component | Responsibility | Key symbols | Depends on → consumed by |
|---|---|---|---|
| `pro/agents/` | Evidence layer: an agent **is** configuration (ADR-0014) — 59 `AgentSpec`s across 5 teams select snapshot slices; one shared `EvidenceAgent` runtime. LLM emits only `{claim, direction, confidence}`; attribution is attached by code from the rendered context (ADR-0015). Prompt-injection fencing via `wrap_untrusted`. Abstention taxonomy separates "no data" from "model broken". | `AgentSpec`, `EvidenceAgent`, `roster.py` (24 technical / 13 macro / 7 news / 8 quant / 9 risk), `render_context`, `ComputedFactorAgent` (P3-03), `GoldVolContextAgent` (P3-09) | contracts, llm_clients → pipeline |
| `pro/pipeline/` | The LangGraph debate graph (§2.1). Deterministic gates decide; LLMs argue. Critic runs self-consistency (majority of N, ties fail closed). Rules mode swaps the judge for deterministic consensus + ADX chop filter. Streaming updates feed the recorder and the live UI timeline. | `build_pro_pipeline`, `stream_pipeline`, `PipelineState`, `gates.py` (`risk_gate`, `trade_quality_gate`, `event_gate`, `conformal_vol_gate`), `schemas.py` (`DebateTurn`, `CriticReport`, `ReflectionNote`, `JudgeVerdict`), `qa.py` (ask-the-record) | agents, analytics, memory → service |
| `pro/memory/` | Append-only market memory: trades→outcomes→lessons, regime records, semantic retrieval (model2vec `potion-base-8M`, 256-dim, hashing fallback), knowledge graph of typed relations, win-stats for Kelly. Doubles as an audit trail. | `ProMemory`, `MemoryRecord`, `SemanticEmbedder`, `InMemoryVectorIndex` (+optional Qdrant, ADR-0020), `KnowledgeGraph` | store → pipeline prepare, service |
| `pro/analytics/` | Deterministic math the LLMs are never allowed to do: position sizing (fixed-risk, capped Kelly), VaR/CVaR + portfolio VaR (P2-05), invalidation/ATR stops, R-multiple ladders, HAR-RV + adaptive conformal intervals (P3-04), regime classification, factor language + IC (P3-03), overfitting stats (PSR/DSR/PBO, P2-02), crowding (P5-04), retro-scoring. | `risk.py`, `conformal.py`, `validation.py`, `factors.py`, `crowding.py`, `retro.py` | numpy/pandas → pipeline, execution, backtest |
| `pro/rl/` | RL advisor — **advisory only** (ADR-0025): produces metrics the prepare node includes as context; never sizes or gates. | `advisor`, `policy`, `trainer` | analytics → pipeline prepare |

### 3.4 Money path

| Component | Responsibility | Key symbols | Depends on → consumed by |
|---|---|---|---|
| `pro/execution/router.py` | Routes accepted recommendations by arming tier. Canary clamps to venue minimum BEFORE validation. Applies `LiveRiskLimits`, portfolio caps, TWAP slicing. Every refusal is an audited event. | `ExecutionRouter`, `_canary_sized`, `_submit_twap` | arming, validation, OMS → service |
| `pro/execution/oms.py` | Order lifecycle: deterministic coids, atomic entry+bracket (reduce-only stop rests on the venue), transport-failure resolve loop (`get_order` before resubmit), crash recovery from the journal. | `OrderManager` | adapters → router |
| `pro/execution/adapters/delta.py` | Delta India venue adapter: HMAC signing (5s validity), client-side token bucket, typed error taxonomy (network/5xx/429 transient vs semantic 4xx terminal), **session idempotency guard** (venue only dedupes open coids — proven live), instrument service (integer contracts, fail-closed on missing products), reads always available (arming gates orders, not reads). | `DeltaAdapter`, `SYMBOL_MAP`, `InstrumentService` | interface.py → OMS, drill, CLI |
| `pro/execution/safety.py`, `deadman.py`, `flatten.py` | The three kill-switch layers: (a) automated risk triggers (CircuitBreaker), (b) manual/file switch (`KILL`, reset is explicit + audited), (c) dead-man on `execution_ok` silence (cancels resting orders; never flattens positions — venue-side stops persist). `emergency_flatten` cancels+closes+disarms everything. | `KillSwitch`, `CircuitBreaker`, `DeadManSwitch`, `cancel_for_safety`, `emergency_flatten` | adapter → service, CLI, drill |
| `pro/arming.py` + `pro/live_config.py` + `pro/preflight.py` + `pro/cli.py` + `pro/drill.py` | Arming tiers (shadow→canary→live) with TTL expiry (silent demotion to paper); `live.yaml` loader refuses missing keys (no silent defaults for real capital; leverage >1 needs an explicit acknowledgment flag); readiness report; the interactive ceremonies (`arm-live`, `disarm`, `flatten`, `status`); the kill-switch drill (testnet-only, refuses unarmed pairs). | `ArmingStore`, `load_live_config`, `go_live_readiness`, `run_drill` | execution → operator |

### 3.5 Serving

| Component | Responsibility | Key symbols | Depends on → consumed by |
|---|---|---|---|
| `pro/service.py` | The production heartbeat: one iteration = snapshot → recorded pipeline run → route → manage open positions (bar-close stops/targets/breakeven/trailing, funding accrual) → report P&L to breaker + memory. `run_lock` serializes loop vs on-demand triggers. Event triggers (P2-06), TCA capture (P3-01), TWAP fills, restart rehydration (REL-01). `run_forever` never dies on iteration errors. | `PaperTradingService.run_once/run_forever`, `PipelineTrigger`, `check_event_triggers`, `rehydrate` | everything above → main.py |
| `pro/main.py` | Entrypoint: builds the service + dashboard state, wires staged live routing (armed pairs only), starts the loop thread + dead-man (execution-health heartbeat) + event-trigger daemon, serves uvicorn. Monitor mode when no LLM key (claude-cli counts a logged-in local CLI). | `main`, `build_service`, `_wire_staged_routing`, `has_llm_key` | service, dashboard → Docker/operator |
| `pro/dashboard/` | Thin FastAPI shell over tested view models (`service.py` view layer): auth (token + Google/Firebase → HttpOnly cookie, P3-05 roles), SSE broadcaster (thread-safe seam, replay ring, ticketed direct mode), market data + TV proxy, runs/timeline/evidence/diff/ask, journal/portfolio/risk/status, intel + calendar, backtest job engine (scripted + LLM with cost gate), prefs/watchlists/alerts/bell, track record (P4-02 public, rate-limited) + listings (P4-03 publish gate), users/tokens/webhooks, `/healthz` `/health/live` `/metrics`, SPA fallback (asset-like misses 404, never HTML-as-JS). | `create_app`, `EventBroadcaster`, `MarketDataService`, `PipelineRecorder`, `PrefsStore`, `IntelService`, `backtest_job.py` | service → frontend |
| `frontend/` | React SPA: 11 lazy routes inside `AppShell`/`AuthGate` (public track record outside), Zustand stores (ui/session/ticker/layout/drawings/live-progress), TanStack Query + Zod-validated API layer, one SSE connection with ticket fallback, TradingView terminal (mirrored library, custom datafeed: Pyth history via proxy + Hermes WS + AI marks), safety chrome that never moves (StatusStrip, banners), honesty primitives (`EmptyState` four states, sample-size thresholds, `Freshness`). | `routes.tsx`, `lib/api/{client,types,queries}`, `lib/sse.ts`, `lib/tv/*`, `features/*`, `components/*` | dashboard API → operator |

### 3.6 Verification & ops

| Component | Responsibility | Key symbols | Depends on → consumed by |
|---|---|---|---|
| `pro/backtest/` (36 modules) | The strategy lab: event-driven engine (decide close i, fill open i+1), broker with costs, walk-forward, optimization, Monte Carlo, meta-labeling, portfolio engine + allocator, 7 native strategies + presets/variants, HTML/PDF reports, LLM-pipeline replay with cost confirmation and per-decision funnel capture. | `engine.py`, `walkforward.py`, `presets.py`, `report_html.py` | ingestion, analytics, pipeline → dashboard jobs, scripts |
| `pro/evals/` | Pre-registered evaluation protocol (`docs/EVAL_PROTOCOL.md`): golden cases with forbidden actions, stability (pass^k), token-matched ablation (P1-02), memorization audit via anonymization (P2-03), rules baseline, graded-outcome corpus (P4-01, fine-tune gated at ≥200), factor mining (P3-03), crowding study (P5-04), quarterly self-assessment (P3-08). Ships in the package because the dashboard's replay uses `scripted.py`. | `harness.py`, `golden.py`, `stability.py`, `anonymize.py`, `rules.py`, `corpus.py` | pipeline → CI, operator |
| `tests/` + `frontend/e2e` | ~190 backend files (`test_pro_<area>.py`), hermetic conftest (fake keys, tmp data dir, live vendors disabled), shared fakes; the execution **conformance suite runs the same cases against the fake AND the real testnet** (caught the post-fill coid reuse double-fill live). Playwright e2e with `PRO_TV_HISTORY_BASE=synthetic` (zero egress). | `conftest.py`, `pro_fakes.py`, `test_pro_execution_conformance.py`, `terminal.spec.ts` | everything → CI |
| `deploy/` + `scripts/` | 4-stage Docker image (frontend build, deps, runtime + Litestream + claude CLI + baked embedder + git sha), staging→smoke→prod deploy script (cost shape: prod 1 vCPU warm singleton, staging scale-to-zero), kill-switch + restore drills, TV library mirror, strategy-lab and benchmark scripts, demo server. | `Dockerfile.pro`, `entrypoint-pro.sh`, `deploy_cloud_run.sh`, `pro_live_drill.sh`, `pro_restore_drill.sh`, `mirror_charting_library.py`, `pro_dashboard_demo.py` | — → GCP / operator |
