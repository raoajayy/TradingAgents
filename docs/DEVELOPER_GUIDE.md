# TradingAgents Pro — Developer Guide

Everything you need to build, run, test, extend, and operate this system.
Companion documents:

| Doc | What it covers |
|---|---|
| [`docs/analysis/ARCHITECTURE_DIAGRAMS.md`](analysis/ARCHITECTURE_DIAGRAMS.md) | System / data-flow / component diagrams + per-component responsibilities |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | Phase-by-phase narrative of how the system grew |
| [`DECISIONS.md`](../DECISIONS.md) | ADR log — read before changing anything structural |
| [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) | Cloud Run + Firebase one-time setup and secrets checklist |
| [`docs/LIVE_PILOT_RUNBOOK.md`](LIVE_PILOT_RUNBOOK.md) | P3-01 live dust pilot: ceremonies, drills, acceptance criteria |
| [`docs/EVAL_PROTOCOL.md`](EVAL_PROTOCOL.md) | Pre-registered evaluation methodology |
| [`docs/DATA_SOURCES.md`](DATA_SOURCES.md) | Free-first vendor decision table |

---

## 1. Orientation

TradingAgents Pro is a multi-agent trading terminal: 59 evidence agents
debate through a LangGraph pipeline, deterministic gates decide, and an
execution layer routes accepted decisions to paper or (when an operator has
run the arming ceremony) a real venue at dust size. One Python process serves
both the trading loop and the FastAPI dashboard; a React SPA consumes it.

Two deployment shapes, same code:

- **Cloud paper** — Cloud Run `pro-dashboard` behind Firebase Hosting.
  Hourly paper loop, SQLite event store replicated to GCS by Litestream.
  Never holds venue trading keys.
- **Local armed host** — the same `python -m tradingagents.pro.main` on an
  operator machine with `live.yaml`, venue testnet keys in `.env`, and pairs
  armed via the CLI ceremony. This is where live (testnet) orders happen.

The one-line philosophy that explains most code you'll read: **LLMs argue,
code decides, failures are disclosed, and every money-touching path fails
closed and leaves an audit record.**

## 2. Repository layout

```
tradingagents/
  contracts/            # typed spine: frozen pydantic models (extra=forbid)
  llm_clients/          # provider factory; with_structured_output is the seam
  pro/
    agents/             # 59 AgentSpecs (config, not code) + one EvidenceAgent runtime
    pipeline/           # LangGraph graph, nodes, deterministic gates, schemas
    ingestion/          # feeds -> MarketSnapshot; missing_feeds discipline
    execution/          # router, OMS, venue adapters, safety (kill/deadman/flatten)
    memory/             # trades->outcomes->lessons, embeddings, knowledge graph
    analytics/          # risk math, conformal vol, regimes, factors, validation
    dashboard/          # FastAPI app + view models + SSE + recorder + stores
    backtest/           # strategy lab: engine, walk-forward, presets, reports
    rl/                 # advisory-only RL (ADR-0025)
    evals/              # golden cases, stability, ablation, anonymization, corpus
    service.py          # the loop: snapshot -> run -> route -> manage positions
    main.py             # entrypoint: service + dashboard + safety daemons
    arming.py, cli.py, drill.py, preflight.py, live_config.py, health.py,
    store.py, memory.py, models.py, observability.py, alerting.py, ...
frontend/
  src/app/              # AppShell, AuthGate, Sidebar, StatusStrip
  src/features/         # one folder per page (home, workspace, decisions, ...)
  src/components/       # shared components (+ charts/, ui/)
  src/lib/              # api client/types/queries, sse, tv/ datafeed, helpers
  src/stores/           # zustand: ui, session, ticker, layout, drawings
  e2e/                  # Playwright spec
  public/charting_library/  # mirrored TradingView library (same-origin!)
deploy/                 # Dockerfile.pro, entrypoint, cloudbuild, k8s, compose
scripts/                # deploy, drills, labs, demo server, mirror script
tests/                  # ~190 pytest files, flat; conftest is hermetic
docs/                   # runbooks, protocol docs, this guide
```

## 3. Local setup

### Backend

```bash
python3.13 -m venv ~/.venvs/tradingagents-pro     # NEVER under ~/Documents —
source ~/.venvs/tradingagents-pro/bin/activate    # iCloud sync corrupts venvs
pip install -e ".[dev]"
```

Secrets live in `.env` at the repo root — **mode 0600, gitignored,
operator-typed** (never paste secrets through chat tools or commit them).
The readiness report (`tradingagents-pro readiness-report`) checks the file
mode. Provider API keys follow the `<PROVIDER>_API_KEY` convention
(`llm_clients/api_key_env.py` is the canonical map); `claude-cli` needs no
key when the local `claude` CLI is logged in.

### Run the demo dashboard (no LLM spend, synthetic data)

```bash
python scripts/pro_dashboard_demo.py 8601
```

### Run the real loop + dashboard

```bash
set -a && source .env && set +a
PRO_DATA_DIR=$HOME/.tradingagents/pro PORT=8600 \
  python -m tradingagents.pro.main
```

Add for live (testnet) routing: `PRO_LIVE_CONFIG=$PWD/live.yaml` plus
Delta testnet keys in `.env`, then arm pairs via the CLI (§9). On a laptop,
wrap in `caffeinate -i` — a sleeping host trips the dead-man switch by
design (the system was genuinely blind).

### Frontend dev

```bash
cd frontend && npm install
PRO_API_TARGET=http://127.0.0.1:8600 npm run dev    # vite proxies /api
npm run build                                        # -> tradingagents/pro/dashboard/static
```

The built SPA is served by FastAPI itself — the demo/loop servers serve
whatever was last built. Two chronic dev gotchas:

- **Service worker cache**: the PWA (`registerType: "prompt"`) keeps serving
  the old bundle until the update prompt is accepted. In dev, unregister the
  SW + clear caches (DevTools → Application) after a rebuild, or you will
  debug phantom old code.
- **Port squatting**: anything else on 8600 (e.g. a forgotten
  `deploy/docker-compose.pro.yml` container publishing `127.0.0.1:8600`)
  silently receives your API calls — macOS lets a localhost-specific socket
  win over a wildcard bind. `lsof -i :8600` before you trust a "hung" server.

## 4. Environment variable reference

There was no central list; this is it. Scope: **L**oop, **D**ashboard,
**O**ps/deploy, **T**ests.

| Variable | Scope | Default | Effect |
|---|---|---|---|
| `TRADINGAGENTS_LLM_PROVIDER` | L | `deepseek` | provider key for the factory (`groq`, `openai`, `claude-cli`, …) |
| `TRADINGAGENTS_QUICK_THINK_LLM` / `_DEEP_THINK_LLM` | L | provider defaults | model ids; teams/debaters→quick, critic/reflection/judge→deep |
| `TRADINGAGENTS_PRO_DATA` | L/D | `~/.tradingagents/pro` (image: `/data`) | data dir: audit.jsonl, arming.json, KILL, prefs, artifacts |
| `TRADINGAGENTS_PRO_DB` | L/D | `<data>/pro.db` (image: `/tmp/pro.db`) | EventStore SQLite path — **local disk only**, never GCS FUSE |
| `TRADINGAGENTS_EMBEDDER` | L | model2vec | `hashing` forces the no-model fallback embedder |
| `PRO_DASHBOARD_TOKEN` | D | unset (=open, localhost dev only) | API auth token; exchanged for an HttpOnly cookie at `/api/session` |
| `PRO_LOOP_DISABLED` | L | `0` | `1` skips the hourly loop; on-demand runs still work |
| `PRO_LOOP_INTERVAL_SECONDS` | L | `3600` | decision cadence |
| `PRO_RERUN_UNCHANGED_BARS` | L | unset | `1` re-runs a symbol even when its driving bar is unchanged |
| `PRO_EVENT_TRIGGERS` | L | unset | `1` enables P2-06 calendar/vol-spike/gap triggered runs |
| `PRO_LIVE_CONFIG` | L | unset | path to `live.yaml`; loader refuses missing keys |
| `PRO_LIVE_EXCHANGE` | L | `delta` | venue adapter selection (`delta` \| `binance`) |
| `PRO_LIVE_VENUE` | L | `testnet` | never `production` in pilot Phase A |
| `PRO_BINANCE_MAINNET_ACK` | L | unset | half of the mainnet double opt-in (separate key names are the other half) |
| `PRO_DEADMAN_TIMEOUT_SECONDS` | L | `600` | dead-man trip threshold on `execution_ok` silence |
| `PRO_MAX_PORTFOLIO_VAR_PCT` / `PRO_MAX_CORRELATED_GROSS_PCT` | L | unset (off) | P2-05 pre-trade portfolio caps |
| `PRO_MAX_VOL_INTERVAL_WIDTH_PCT` / `PRO_VOL_INTERVAL_SIZE_SCALE` | L | unset (off) | P3-04 conformal vol gate: block, or scale size on breach |
| `PRO_TWAP_SLICES` / `PRO_TWAP_WINDOW_MIN` | L | unset (off) | P3-10 entry slicing |
| `PRO_MAX_RUNS` | L/D | `500` | recorder retention / boot RAM |
| `PRO_REQUIRE_PINNED_MODELS` | L | unset | refuse floating model aliases (AI-07) |
| `PRO_TV_HISTORY_BASE` | D/T | `https://thefundedroom.com` | Pyth history upstream; `synthetic` = deterministic offline bars (CI) |
| `PRO_BACKTEST_STORE` | D | file | `firestore` selects the Firestore run store |
| `PRO_PUBLIC_TRACK_RECORD` | D | unset (off) | enables `/public/v1/*` (legal gate) |
| `PRO_PUBLIC_RATE_LIMIT` | D | sane default | public endpoint rate limit |
| `PRO_LISTING_MIN_GRADED` | D | `30` | P4-03 publish gate: graded outcomes required |
| `PRO_ALLOWED_EMAILS` / `PRO_FIREBASE_PROJECT_ID` / `PRO_FIREBASE_WEB_CONFIG` | D | unset | Google sign-in allowlist + web config |
| `PRO_STREAM_DIRECT_URL` / `PRO_STREAM_ALLOWED_ORIGIN` | D | unset | ticketed direct-SSE mode (Firebase proxy buffers streams) |
| `PRO_TELEGRAM_BOT_TOKEN` / `PRO_TELEGRAM_CHAT_ID` | L/D | unset | Telegram alert sink |
| `PRO_ALERT_WEBHOOK_URL` | L/D | unset | webhook alert sink (HMAC-signed) |
| `PRO_DISABLE_LIVE_VENDORS` / `PRO_DISABLE_LIQUIDATION_STREAM` | T | set by conftest/e2e | hermetic tests: no vendor egress / no WS |
| `PRO_DASHBOARD_DEV` | D | unset | dev conveniences |
| `PRO_DEMO_ARM` | D | unset | demo server: pre-arm `PAIR:tier` |
| `PRO_PYTHON` | T/O | `python` | interpreter for Playwright's webServer + scripts |
| `LITESTREAM_REPLICA_URL` | O | unset | enables restore-then-replicate in the entrypoint |
| `GIT_SHA` | O | baked file wins | provenance stamp (P3-07) |
| `CLAUDE_CODE_OAUTH_TOKEN` | L/O | unset | headless claude-cli auth (local login suffices on workstations) |
| Vendor keys | L/D | unset | `DELTA_TESTNET_API_KEY/_SECRET`, `DELTA_API_KEY/_SECRET`, `BINANCE_TESTNET_*`, `OANDA_API_TOKEN`, `FRED_API_KEY`, `TOKENTERMINAL_API_KEY` |

## 5. Testing

| Suite | Command | Notes |
|---|---|---|
| Backend unit/integration | `python -m pytest tests/ -q` | hermetic by default: conftest injects fake provider keys, tmp data dir, disables live vendors + liquidation WS |
| Execution conformance | `python -m pytest tests/test_pro_execution_conformance.py` | the same cases run against the **fake venue AND the real Delta testnet** (needs `DELTA_TESTNET_*`). This dual run is how the post-fill coid-reuse double-fill was caught live — keep it dual. |
| Ruff | `ruff check tradingagents tests scripts` | zero tolerance |
| Frontend unit | `cd frontend && npx vitest run` | colocated `src/**/__tests__` |
| Types/lint | `npx tsc -b && npx eslint src e2e --max-warnings 0` | |
| Browser e2e | `PRO_E2E_PORT=8614 PRO_PYTHON=$HOME/.venvs/tradingagents-pro/bin/python npx playwright test` | boots the demo server with `PRO_TV_HISTORY_BASE=synthetic` — zero network egress; TV assertions are iframe-based (`chartFrame`) |
| Evals | `python -m tradingagents.pro.evals --help` | real-model runs; follow `docs/EVAL_PROTOCOL.md` (pre-registered) |

Ship gate: ruff + pytest + tsc + eslint + vitest + build + the Playwright
suite green. Live-API tests (e.g. `test_deepseek_reasoning.py`) depend on the
configured provider — deselect if you've switched providers.

Shared fixtures: `tests/pro_fakes.py` (`FakeTransport`, `make_bars` with
valid OHLC geometry), `tests/conftest.py` (three autouse hermetic fixtures —
read it before fighting a mysteriously-missing env var).

## 6. Conventions & invariants (the house rules)

**Honesty norms**
- Data a feed couldn't provide is *absent and disclosed* (`missing_feeds`),
  never interpolated. Agents get a `missing_note` and abstain on empty
  context; the UI renders one of four honest `EmptyState`s, never a fake.
- Sample-size honesty: stats under the thresholds in
  `frontend/src/lib/thresholds.ts` render with caveats, not headlines.
- The UI renders server-computed numbers; it never re-derives trading math
  (Constraint 2).

**Safety invariants** (each has tests; several were earned live)
- *Arming gates orders, not reads* — a disarmed operator can always see and
  flatten the book.
- *Entry + stop are atomic*; the protective stop rests **on the venue**, so
  it survives process death.
- *All gates fail closed*: unknown = refuse (canary sizing, live gates,
  critic ties, missing live.yaml keys, missing instrument info).
- *Kill switch layers*: automated triggers → manual/file `KILL` → dead-man
  on `execution_ok` silence. Reset is an explicit, audited operator action.
  Advisory health checks (`feeds`, `models`) never trip the dead-man.
- *Idempotency is layered*: OMS resolve-by-coid, plus the adapter's session
  guard (venues may free coids after fill — Delta does).
- *Single writer* everywhere stateful: one uvicorn worker, one SQLite writer
  connection, one loop process per data dir.
- *Hash-chained audit* (`audit.jsonl`) records every order event, refusal,
  arming change, kill-switch action, and reconciliation — with P3-07
  version stamps (`git_sha`, `prompt_hash`, `model_ids`, `config_hash`).

**Code discipline**
- Contracts are frozen, `extra="forbid"`; breaking changes bump
  `SCHEMA_VERSION` and get an ADR.
- The LLM seam is `with_structured_output(schema)` — never parse free text.
- Secrets: redacted from every exception/log (`redact()`), `NAME_FILE`
  resolution supported, operator-typed into `.env`/Secret Manager only.
- Comments explain constraints the code can't show; prompts are versioned
  files hashed into run stamps.
- Structural decisions get an ADR in `DECISIONS.md`.

## 7. Extension recipes

### 7a. Add a tradeable symbol (the exact ETH-USD trail)
1. **Registry**: `pro/dashboard/marketdata.py` `default_registry()` — add a
   `crypto_spec(...)` (or bespoke `SymbolSpec`) with vendor symbols.
2. **Pyth mapping**: add to `PYTH_SYMBOLS` (now in `pro/ingestion/pyth.py`)
   so charts + TV datafeed resolve it.
3. **Venue adapter**: `pro/execution/adapters/delta.py` `SYMBOL_MAP` — and
   extend the fake venue products in
   `tests/test_pro_execution_conformance.py` (ids/ticks/contract values must
   mirror the real testnet).
4. **Arming allowlist**: `pro/arming.py` `ArmingStore(pairs=...)`.
5. **Pipeline wiring**: `pro/main.py` `CRYPTO_WIRING`/`FX_WIRING` +
   `PipelineTrigger.SYMBOLS`.
6. **Sanity-check venue minimums vs your notional cap** — canary clamps to
   the venue minimum, and validation refuses if min > cap (BTC min ≈ $65
   needs equity×alloc%×leverage ≥ that).
7. Operator side: `live.yaml` pair entry → `readiness-report` →
   `arm-live` ceremony.

### 7b. Add a data feed
1. Implement one of the `base.py` Protocols (`BarsFeed`/`QuoteFeed`/
   `MetricsFeed`) with an injectable transport; raise on failure — never
   return fabricated data. Map rate limits to `VendorRateLimitError`.
2. Wire into the relevant builder factory (`build_gold_pipeline` /
   `_crypto_snapshot_builder` in `pro/main.py`) — failures automatically
   land in `missing_feeds`.
3. Test with `FakeTransport` canned payloads; add a failure-path test
   asserting the `missing_feeds` entry.
4. Record the sourcing decision in `docs/DATA_SOURCES.md`.

### 7c. Add an evidence agent
Add an `AgentSpec` to `pro/agents/roster.py` — agents are config, not code
(ADR-0014). Pick team, persona, and snapshot selectors (`indicators`,
`metrics`, `include_news`, …); a spec that selects nothing is rejected at
import. Team prompt templates live in `pro/agents/prompts/<team>_team.md`.
Deterministic (non-LLM) agents follow `ComputedFactorAgent`.

### 7d. Add a pipeline node or gate
- Gates: pure functions in `pro/pipeline/gates.py` returning `GateResult`;
  call them from `nodes.py`; record into `gate_results` (the UI's
  GateWaterfall picks them up automatically).
- Nodes: add to `pro/pipeline/graph.py` wiring + a structured-output schema
  in `schemas.py`; map the node name into the frontend vocabulary at
  `frontend/src/lib/pipelineStages.ts` or the 3D board won't know the stage.

### 7e. Add a venue adapter
Implement the Protocols in `pro/execution/interface.py` (`place_order`,
`cancel_order`, `get_order`, `positions`, `account`, `supported_symbols`,
`capabilities`, plus `mark_price`/`has_resting_stop` for the drill).
**Parametrize it into the conformance harness** (fake + real testnet) — the
suite encodes every lesson (idempotency, below-minimum rejection, crash
recovery, amnesia rebuild). A new adapter is not done until the kill-switch
drill passes against its testnet.

### 7f. Add a dashboard endpoint
View-model function in `pro/dashboard/service.py` (pure, unit-tested) → thin
route in `app.py` (sync if it blocks on vendors — Starlette threadpool) →
Zod schema in `frontend/src/lib/api/types.ts` → TanStack hook in
`queries.ts` → pytest for the view model + route, e2e if user-visible.

### 7g. Add a frontend page/widget
Pages: lazy route in `routes.tsx` via the `page()` helper. Widgets: register
in the page's `WIDGETS` + `WidgetGrid`; bump `LAYOUT_VERSION` in
`stores/layout.ts` when changing default layouts (one-time reset of saved
layouts). Keep `data-testid`s stable — the e2e suite navigates by them.

## 8. Deploy & ops

```bash
PROJECT_ID=<gcp-project> BUCKET=<bucket> ./scripts/deploy_cloud_run.sh
```

Flow: Cloud Build image (one build) → deploy **staging** (always
loop-disabled, scale-to-zero, throttled CPU) → HTTP smoke check
(`health_feeds_only`: feeds-degradation acceptable, anything else aborts) →
promote to **prod** (warm singleton, 1 vCPU, unthrottled — the loop and
dead-man run outside requests). `--update-env-vars`/`--update-secrets`
merge, so out-of-band settings survive redeploys.

- **Cost shape** (post-audit): staging ≈ $0 idle; prod ≈ 1 always-on vCPU.
  Never redeploy staging with `min-instances=1` — that was half the bill.
- **Durability**: Litestream restore-then-replicate in the entrypoint;
  prove it quarterly with `scripts/pro_restore_drill.sh`.
- **Monitoring**: `/metrics` (Prometheus text), `/health/live` (full
  verdict; `execution_ok` is what the dead-man consumes), alert sinks.
- **PWA**: users must accept the update prompt after a deploy (deliberate —
  never silently swap a trading UI).
- **TradingView library re-sync**: `python scripts/mirror_charting_library.py`
  (evaluates the webpack runtime manifest with node; HTML payloads on asset
  paths are hard errors).

## 9. Live trading operations (P3-01)

The runbook is [`docs/LIVE_PILOT_RUNBOOK.md`](LIVE_PILOT_RUNBOOK.md); these
are the operational lessons already paid for:

- **Ceremonies**: `readiness-report` → `arm-live --config live.yaml --pair X
  --operator <you> --ttl-days 14` (typed confirmation phrase; canary first).
  Arming expires silently to paper. `disarm`/`flatten` are never gated.
- **Venue minimum vs notional cap**: canary clamps to the venue minimum;
  validation refuses if minimum > equity×alloc%×leverage. Check the table
  before arming a pair on a small account (Delta: BTC ≈ $65, SOL ≈ $78,
  ETH ≈ $19, gold ≈ $4.3 minimums). Leverage >1 in `live.yaml` requires the
  explicit `i_understand_leverage_multiplies_losses: true`.
- **Laptop hosts**: sleep = the dead-man trips on wake (correctly — the
  system was blind). Run under `caffeinate -i`, keep power connected, or use
  an always-on host. The dead-man cancels resting orders and engages the
  kill switch; positions keep their venue-side stops.
- **Kill-switch reset** is a ceremony: verify the venue is flat first, reset
  with an operator identity, append an audited `kill_switch_reset` record
  with the reason.
- **Drill cadence**: re-run `scripts/pro_live_drill.sh` after ANY
  execution-layer change and at least monthly. It disarms all pairs at the
  end — re-arm afterwards.
- **Manual trading on the same account** pollutes reconciliation
  (`unknown_on_venue`) and the 10-fill evidence. Don't.

## 10. Troubleshooting (earned the hard way)

| Symptom | Cause | Fix |
|---|---|---|
| TV chart area blank, toolbars fine | hidden/occluded tab — Chrome suspends canvas painting (ResizeObserver/rAF frozen) | front the tab; it paints instantly. Not a bug. |
| Old UI after deploy/build | PWA service worker serving the previous bundle | accept the update prompt; in dev, unregister SW + clear caches |
| `Uncaught SyntaxError: Unexpected token '<'` in chart chunks | a missing `/charting_library/bundles/*` file served as HTML | re-run the mirror script; the SPA fallback now 404s asset-like misses — if you see this, the mirror is incomplete |
| Loop boots in "monitor mode" | `has_llm_key()` false for the configured provider | set `<PROVIDER>_API_KEY`; for `claude-cli`, a logged-in local CLI counts |
| Dead-man trips ~600s after arming | pre-fix: optional feed outage starved the heartbeat; now only `execution_ok` checks count. Post-fix trips are real (venue/clock/sleep) | check `pmset -g log` for sleep; check `/health/live` |
| API "hangs", no logs at all | something else owns the port (Docker publishing `127.0.0.1:8600` beats a wildcard bind) | `lsof -i :8600`; stop the squatter |
| Many agents "abstaining", thin debates, critic rejections | LLM provider rate limits (groq free tier: 429 storms) | switch provider / paid tier; abstentions are honest, not a bug |
| `arm-live`: "X is not configured" / "unknown pair" | missing `live.yaml` pair entry / not in `ArmingStore` pairs | add both (recipe 7a) |
| `validation_failed: notional X exceeds cap Y` | venue minimum order > your cap at current equity | bigger account, higher alloc%/leverage (acknowledged), or a cheaper-minimum pair |
| `reconciliation in_sync: false, unknown_on_venue` | orders/positions placed outside the system (manual UI trading, test residue) | close them; investigate before resetting anything |
| Delta testnet clock complaints | the HTTP `Date` header is CDN-edge time — not the signing clock | trust signature validation; the adapter already warns instead of refusing |
| SQLite "database is locked" in prod | event store on GCS FUSE or a second writer | keep `TRADINGAGENTS_PRO_DB` on local disk; one process per data dir |
