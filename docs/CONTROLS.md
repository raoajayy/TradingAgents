# Controls (SOC2-track) — P3-12

Honest, evidence-based description of the operational controls that exist today.
Every claim cites the code that implements it and the test that proves it (see the
appendix). Where a control has a hole, the hole is stated. This is a single-operator
deployment (Cloud Run, one instance); several controls are scoped accordingly.

Status: living document. Last verified against the codebase on 2026-07-31
(branch `pro/phase-0-contracts`; `tests/test_pro_persistence.py` green, 9 passed).

## 1. Access control

**Dashboard auth (X-API-Key).** All `/api/*` routes require the `X-API-Key` header
(or the session cookie it mints) when `PRO_DASHBOARD_TOKEN` is set
(`tradingagents/pro/dashboard/app.py:7-11`, comparison via `hmac.compare_digest`,
`app.py:252, 268`). This is a single deployment-level operator token — one secret,
one holder. It is **not per-person attributable** (see Gaps). It always resolves to
full operator (`app.py:26-28, 261-263`).

**Google sign-in + allowlist.** When `PRO_FIREBASE_PROJECT_ID` *and* a non-empty
`PRO_ALLOWED_EMAILS` allowlist are both set, `POST /api/session` accepts a Google
ID token, verified against Google's public certs and gated on `email_verified` +
the allowlist (`app.py:13-18, 80-84, 212-221`). Fail closed: a project id without
an allowlist keeps Google sign-in disabled (`app.py:18-19, 221`).

**Roles (P3-05).** Session JWTs carry a `role` claim, `viewer` or `operator`
(`app.py:22-31, 169-181`). Roles live in a `users` table — `(email PK, role,
created_at)` with a CHECK constraint on role (`tradingagents/pro/store.py:71-74,
217-262`). Allowlist emails are seeded as operators once, when the table is empty
(`app.py:223-244`); an allowlisted email *absent* from the table defaults to
operator (`app.py:234-244`) — a deliberate but permissive default. Mutating verbs (POST/PUT/DELETE) under `/api` require operator;
viewers get 403; every GET stays viewer-readable (`app.py:29-31`). **Role
demotion is immediate on the mutation path**: the auth middleware re-resolves
the role from the `users` table on every mutating request
(`app.py::_request_role(fresh=True)`, called from `require_api_key`), so a
demoted operator loses POST/PUT/DELETE rights on their very next request even
with a still-valid 7-day session JWT
(`tests/test_pro_dashboard_app.py::test_demoted_operator_loses_mutations_immediately`).
**Accepted read-path lag:** GETs keep the fast stateless path — they trust the
signed `role` claim until the session re-establishes (at most the 7-day JWT
TTL). This is deliberate: reads are viewer-grade anyway, so a stale operator
claim grants nothing a viewer would not already have.

**Public API tokens (P3-11).** `/public/v1/*` uses separate bearer tokens, minted
by operators via `/api/tokens` (`app.py:39-41`). Only the sha256 hash is stored
(`api_tokens` table: token_hash PK, label, scopes csv, created_at, revoked_at,
expires_at — `store.py::_SCHEMA`, `store.py::create_api_token`); the raw token
is returned exactly once at creation. Scopes are an allowlist of
`read:decisions`, `read:calibration`, enforced per endpoint; revocation is a
`revoked_at` timestamp. **Expiry:** `POST /api/tokens` accepts an optional
`expires_days` (> 0) that stamps a nullable `expires_at`; an expired token is
rejected with 401 "token expired" (`store.py::resolve_api_token` returns it
with `expired: True` and zero scopes; `app.py::_public_auth` maps that to the
401) and the expiry shows in the token list. Tokens minted without
`expires_days` keep the old semantics (live until revoked). The column is an
additive upgrade — a guarded `ALTER TABLE api_tokens ADD COLUMN expires_at`
runs on every store open, so pre-existing production DBs upgrade in place
(`store.py::EventStore.__init__`;
`tests/test_pro_public_api.py::TestTokenExpiry`). Requests are
rate-limited per token by an in-process token bucket (`PRO_PUBLIC_RATE_LIMIT`
req/min, default 60; `app.py:39-41, 1348-1404`). Public tokens never grant `/api`
access and session cookies never grant `/public/v1` access.

**Public track record (P4-02).** `/public/v1/track-record` (scope
`read:decisions`) and its pre-auth SPA page `/public/track-record` are
feature-flagged **off by default pending legal counsel**: both 404 unless
`PRO_PUBLIC_TRACK_RECORD=1` is set at startup (`dashboard/app.py`). The page
authenticates with an operator-issued `read:decisions` token supplied as
`?token=` or baked in at build time via `VITE_TRACK_RECORD_TOKEN`.

**Secrets.** Runtime resolution is `NAME_FILE` (mounted file) then `NAME` env var
(`tradingagents/pro/secrets.py:1-40`); secrets are never logged (`describe_source`
reports origin without the value). In the Cloud Run deployment, secrets live in
GCP Secret Manager and are wired with `--update-secrets` (dashboard token +
LLM provider key at `scripts/deploy_cloud_run.sh:189`; Telegram alerting secrets
applied out-of-band and preserved by merge semantics, `deploy_cloud_run.sh:154-158`).
*Not evidenced in-repo:* per-secret IAM bindings are GCP-side configuration; the
repo contains no `gcloud secrets add-iam-policy-binding` step, so per-secret least
privilege must be evidenced from GCP console/audit, not from this codebase.

**What this does not cover:** no MFA enforcement (delegated to Google); no session
revocation list — session JWTs (7-day TTL, `app.py:154`) can only be invalidated by
rotating `PRO_DASHBOARD_TOKEN` (though a role demotion now revokes mutation
rights immediately — only read access rides out the cookie, see Roles above);
no per-user API keys. Cloud Run itself is deployed
`--allow-unauthenticated` (`deploy_cloud_run.sh:190`) — the app-level token is the
sole perimeter.

## 2. Change management

**Version stamps (P3-07).** Every recorded run carries a `versions` stamp —
`git_sha`, `prompt_hash` (sha256 over the five pipeline prompt templates),
`config_hash` (sha256 of the canonicalised config, volatile fields excluded), and
`model_ids` (`tradingagents/pro/versioning.py:36-113`). Runs are stamped in the
recorder (`tradingagents/pro/dashboard/recorder.py:296-303`), orders/audit events
in the execution router (`tradingagents/pro/execution/router.py:71, 84-88, 96, 185,
404, 431`). Stamping is fail-open: an exception logs and the run records unstamped
(`recorder.py:302-303`). **Known weakness:** nothing injects `GIT_SHA` into the
production image and the image has no `.git`, so `git_sha` in prod likely resolves
to the literal `"unknown"` (`versioning.py:47-61`; no GIT_SHA in `deploy/` or CI).

**CI gates (`.github/workflows/pro-ci.yml`).** Triggers: push to `pro/**` and
`main`, plus all PRs (lines 5-8). Jobs:
- `lint-and-test` — ruff, `pip-audit` on the lockfile, SBOM artifact, full
  `pytest -q`, evals tests (lines 11-42).
- `frontend` — npm audit (high, prod deps), tsc, lint, tests, build, gzip
  bundle-size gate < 200 KiB (lines 44-79).
- `e2e` — Playwright desktop suite against the built SPA (lines 81-117).
- `docker-build` — builds `deploy/Dockerfile.pro` on push; build-only, no registry
  push (lines 119-126).
- `venue-testnet` — exchange conformance tests, only when testnet keys exist;
  never a required check (lines 128-151).
Branch protection / required checks are GitHub-side settings, not evidenced in-repo.

**Deploy flow (`scripts/deploy_cloud_run.sh`).** Operator-run (no CI deploy job).
Order: build once via Cloud Build, image tagged with the local `git rev-parse
--short HEAD` (lines 110-133) → deploy to **staging** (own service + own bucket,
loop disabled, lines 251-256) → **smoke check**: up to 12 attempts on
`/health/live` (200, or 503 where the only failing check is `feeds`) plus
`/api/auth/config` 200 (lines 203-249) → on failure, prod deploy aborts (lines
258-261); on pass, prod deploys (line 265). **Escape hatch:** `SKIP_STAGING=1`
bypasses the gate (documented emergency-only, lines 33-37, 251-252). There is no
manual-approval step and no automated rollback in the script; the CI-tested image
and the deployed image share a git sha but are separate builds.

**Audit hash chain.** Execution events append to `data/audit.jsonl` as a
sha256-linked chain: each entry's hash covers `{seq, ts, event, payload,
prev_hash}`; genesis is 64 zeroes; every append is fsynced
(`tradingagents/pro/execution/audit.py:17-57`,
`tradingagents/pro/persistence.py:42`). `verify()` returns a bool: it recomputes
every hash and checks seq ordering and prev-hash linkage (`audit.py:59-69`). It
**detects** in-place edits, reordering, and mid-chain deletion. It does **not
detect**: truncation from the end (a shorter chain is still valid — no external
length anchor), wholesale rewrite (the chain is unsigned; anyone with write access
can regenerate it from genesis — it is tamper-*evident*, not tamper-proof), or
deletion of the whole file. **Scheduled integrity check:** the service loop
re-reads the audit JSONL from disk and runs `verify()` once per UTC day
(`tradingagents/pro/service.py::_maybe_verify_audit`, called from `_run_once`).
A failure — or an unreadable/corrupt file — raises a CRITICAL
`audit_integrity` alert ("audit chain integrity violation") and increments
`audit_verify_failures_total`; a pass refreshes the `last_audit_verify_ts`
gauge, both visible on `/metrics`. The P3-08 self-assessment cites this
evidence in its open-risks section
(`evals/self_assessment.py`). Proven by
`tests/test_pro_e2e_service.py::TestAuditVerifySchedule`. The go-live readiness
report, arming ceremonies, reconciliation passes, dead-man trips and emergency
flattens are all written into this chain (`preflight.py:163-179`, `arming.py:57-61`,
`router.py:310-315`, `deadman.py:116`, `flatten.py:63`).

## 3. Backup and restore (P2-01)

**Continuous replication.** The event store is SQLite on local disk
(`/tmp/pro.db`; GCS FUSE cannot hold SQLite locks) replicated by Litestream to
`gcs://<bucket>/litestream` (`deploy/Dockerfile.pro:37-45`,
`deploy/entrypoint-pro.sh:15-23`, `scripts/deploy_cloud_run.sh:166`). On boot the
entrypoint restores from the replica if the local DB is missing, then runs the
service under `litestream replicate -exec`. Staging replicates to its own bucket.
Sync interval is Litestream's default (~1s); it is not explicitly configured.
Single-writer is enforced by `--max-instances 1` (`deploy_cloud_run.sh:181-182`).

**Restore drill.** `scripts/pro_restore_drill.sh` simulates "container deleted":
restore the replica to a scratch path, export to JSONL, eyeball counts against the
live dashboard. The first drill is recorded in the script header: **2026-07-29,
PASSED — 322 runs / 617 memory rows / prefs restored purely from the bucket**
(lines 10-12). Documented cadence is quarterly (`docs/DEPLOYMENT.md`); only that
one drill is evidenced so far, and the evidence lives in a comment, not a log.

**Format pin.** Prod writes Litestream v0.3 replica format; v0.5 cannot read it
(and renamed `gcs://` to `gs://`). The image pins the v0.3 binary
(`Dockerfile.pro:45` — a floating minor tag, not a digest) and the drill script
warns operators (`pro_restore_drill.sh:12-14`).

**JSONL export.** `python -m tradingagents.pro.store export [--out DIR]` dumps
`runs.jsonl`, `memory.jsonl`, and `dashboard_prefs.json`
(`tradingagents/pro/store.py:518-559`) — the human-inspectable escape format used
by the drill.

## 4. Incident detection and monitoring

**Metrics.** `GET /metrics` serves Prometheus text exposition v0.0.4 from a
dependency-free registry (`tradingagents/pro/observability.py:47-101`,
`dashboard/app.py:485-492`). Families include `runs_total`,
`rejections_total{stage}`, `orders_filled_total`, `reconciliation_failures_total`,
`iteration_errors_total`, `alerts_total{severity,event}`,
`alert_delivery_failures_total{sink}`, gauges `last_run_ts` and
`llm_est_cost_usd` (estimates, not billed usage). `/metrics` and `/healthz` are
deliberately unauthenticated (counters only, no payload data — `app.py:487-488`).

**Alerting.** Three severities — `critical` (pages: kill switch, breaker trip,
reconciliation drift, quarantined injection), `warning` (reviewed daily), `info`
(`tradingagents/pro/alerting.py:8-11, 25`). `AlertManager` fans out to sinks with
per-sink isolation — a failing sink is counted and dropped, never raised into the
trading loop (`alerting.py:135-155`). `TelegramAlertSink` pushes via the Bot API
with token redaction on failure (`alerting.py:94-132`); it is wired only when
`PRO_TELEGRAM_BOT_TOKEN` + `PRO_TELEGRAM_CHAT_ID` secrets exist
(`tradingagents/pro/main.py:290-319`).

**Health.** `/healthz` is bare liveness; `/health/live` aggregates guarded checks —
degraded feeds (newest run's `missing_feeds`), run recency (heartbeat vs 5400s),
venue reachability, clock skew, kill switch — and returns 503 when any fail
(`tradingagents/pro/health.py:22, 54-106`, `app.py:471-483`). Semantics are binary
ok/not-ok with failing check names listed as `degraded`; the loop treats degraded
as "block NEW entries, never exit" (`health.py:10`), and with live gates armed,
missing feeds and stale bars are hard pre-trade stops audited as
`blocked:data_health` / `blocked:stale_data` (`service.py:390-431`).

**Trading safety controls.** Kill switch: file-backed and latching — an operator
can `touch <data>/KILL` from a shell; reset requires an operator identity
(`tradingagents/pro/execution/safety.py:18-52`, path wired at `main.py:501`).
Circuit breaker: latches on N consecutive losses or daily loss ≥
`max_daily_loss_pct` of equity base (`safety.py:87-111`). Dead-man switch: health
unconfirmed for 600s → cancel resting orders (not flatten) + critical alert
(`deadman.py:26-80`). Loss limits: daily/weekly/drawdown gates persisted across
restarts; breach response is cancel-all + flatten + kill switch
(`execution/live_gates.py:152-263, 292+`). Reconciliation: venue positions vs
local book each pass; drift raises a critical alert, blocks orders, and is written
to the audit chain (`execution/router.py:274-316`, `service.py:311-319, 382-384`);
operator remediation via `python -m tradingagents.pro.cli reconcile
[--accept-venue]` (`cli.py:248-269`).

**Self-assessment (P3-08).** `generate_self_assessment` renders a quarterly
RTS-6-flavored markdown doc entirely from stored records — algorithms run (version
stamps), limits fired, kill-switch/breaker/reconciliation events, incidents from
critical alerts, changes (git_sha/prompt_hash transitions), open risks.
Deterministic, zero-LLM; empty periods say "no activity"
(`tradingagents/pro/evals/self_assessment.py:1-34, 74-236`).

## 5. Known gaps (honest list)

Three former gaps are now controls (see §1 and §2): scheduled audit-chain
verification (daily in-loop `verify()` with CRITICAL alert + metrics), public
API token expiry (`expires_at` + `expires_days`, 401 "token expired"), and
immediate role demotion on mutating requests (per-request users-table
re-resolution; the read-path lag is documented as accepted in §1).

1. **The operator token is not per-person attributable.** `X-API-Key` is one shared
   secret with full operator rights; audit events from it cannot be tied to a human.
   Google sign-in adds identity for dashboard users, but the token path remains.
2. **No SIEM / log aggregation beyond Cloud Logging.** No alert-on-log-pattern, no
   anomaly detection, no retention policy under our control.
3. **Rate limiting is in-process** (`app.py:1348`): buckets reset on restart and do
   not aggregate across instances (acceptable at max-instances=1; wrong beyond it).
4. **Secrets rotation is manual.** No rotation schedule or automation. Worked
   incident example: on 2026-07-30 a malformed secret caused the claude-cli error
   path to echo the Authorization header — token included — into Cloud Logging.
   Response: token revoked/re-minted, error paths now scrub `sk-ant-*`/Bearer
   shapes before raising, regression-pinned by test (commit `a5f8348`,
   `tradingagents/llm_clients/claude_cli_client.py`, `tests/test_llm_claude_cli.py`).
5. **Litestream restore-format pin**: prod replicas are v0.3-format; a v0.5 binary
   cannot restore them. Mitigated by the image pin and drill-script warning; the
   pin is a floating `0.3` tag, not a digest.
6. **Audit chain limits**: unsigned (tamper-evident only), end-truncation is
   undetectable. (`verify()` no longer runs only in tests — the service loop
   verifies the on-disk chain daily; see §2. The unsigned/truncation
   weaknesses remain.)
7. **`git_sha` in production stamps is likely `"unknown"`** — the build does not
   inject `GIT_SHA` (see §2).
8. **Deploys are operator-run** with no approval workflow, no rollback automation,
   and a `SKIP_STAGING=1` bypass; branch protection is not evidenced in-repo.
9. **Restore-drill evidence is a script comment**, one drill so far; no
   machine-readable quarterly drill log.

## 6. Evidence appendix

| Control | Implementation | Proving tests |
|---|---|---|
| X-API-Key auth on `/api/*` | `dashboard/app.py:7-11, 249-258` | `tests/test_pro_dashboard_app.py::test_token_auth_unchanged_full_operator` (:695) |
| Google sign-in, fail-closed allowlist | `dashboard/app.py:80-84, 212-221` | `tests/test_pro_dashboard_app.py:603-655` (allowlist seeding, role claim) |
| Viewer/operator role gate | `dashboard/app.py:169-181, 260-279`; `store.py:71-74, 217-262` | `test_pro_dashboard_app.py::test_viewer_reads_but_cannot_mutate` (:631), `::test_operator_passes_the_mutation_gate` (:655), `::test_demoted_operator_loses_mutations_immediately` (:735); `tests/test_pro_store.py::TestUsers` (:235) |
| Public tokens: hash-only, scopes, revocation, per-token rate limit | `store.py:76-81, 264-299`; `dashboard/app.py:1348-1425` | `tests/test_pro_public_api.py` :71, :107, :116, :135, :154, :168, :251, :263 |
| Public token expiry (`expires_days` → `expires_at`, 401 "token expired", additive column upgrade) | `store.py::create_api_token/resolve_api_token/EventStore.__init__`; `dashboard/app.py::create_api_token/_public_auth` | `tests/test_pro_public_api.py::TestTokenExpiry` |
| Immediate role demotion on mutations (per-request users-table re-resolution) | `dashboard/app.py::_request_role(fresh=True)` + `require_api_key` middleware | `tests/test_pro_dashboard_app.py::test_demoted_operator_loses_mutations_immediately` |
| Scheduled audit-chain verification (daily in-loop, CRITICAL alert + `audit_verify_failures_total` / `last_audit_verify_ts`) | `pro/service.py::_maybe_verify_audit`; evidence cited by `evals/self_assessment.py` | `tests/test_pro_e2e_service.py::TestAuditVerifySchedule` |
| Secrets resolution (file/env, no logging) | `pro/secrets.py`; `scripts/deploy_cloud_run.sh:189` | `tests/test_api_key_env.py` (provider-key mapping) |
| Version stamps on runs/orders | `pro/versioning.py:36-119`; `recorder.py:296-303`; `execution/router.py:84-88` | `tests/test_pro_versioning.py` (13 tests: stamp shape, hash sensitivity, run/order stamping, legacy parse) |
| CI gates | `.github/workflows/pro-ci.yml` (5 jobs) | CI run history (GitHub-side) |
| Staging→smoke→prod deploy | `scripts/deploy_cloud_run.sh:203-265` | manual; smoke gate exits non-zero on failure |
| Audit hash chain + verify() | `execution/audit.py:17-69`; `persistence.py:42` | `tests/test_pro_execution_safety.py::TestAuditLog` (:81, :93, :107); chain asserted in `test_pro_persistence.py:135`, `test_pro_live_gates.py:228`, `test_pro_oms.py:297`, `test_pro_e2e_service.py:94` |
| Litestream replicate/restore | `deploy/Dockerfile.pro:37-45`; `deploy/entrypoint-pro.sh:15-23` | restore drill 2026-07-29 PASSED (`scripts/pro_restore_drill.sh:10-12`) |
| JSONL export | `store.py:518-559` | `tests/test_pro_store.py::TestExport` (:142) |
| Durability primitives (fsync, atomic write, restart survival) | `pro/persistence.py` | `tests/test_pro_persistence.py` (9 tests, green 2026-07-31) |
| /metrics Prometheus exposition | `observability.py:47-101`; `app.py:485-492` | `tests/test_pro_observability.py:41-72`; `test_pro_dashboard_app.py:541` |
| Alert severities, sink isolation, Telegram | `alerting.py:25, 94-155`; `main.py:290-319` | `tests/test_pro_alerting.py` (:30-:169); `tests/test_pro_health_alerting.py` (:30-:83) |
| Health endpoint 503-when-degraded | `health.py:54-106`; `app.py:475-483` | `tests/test_pro_health_alerting.py:148-192` |
| Kill switch (file-backed, latching) | `execution/safety.py:18-52` | `tests/test_pro_execution_safety.py:13-36` |
| Circuit breaker | `execution/safety.py:61-111` | `test_pro_execution_safety.py:38-79`; `tests/test_pro_risk_breaker.py:71, 80` |
| Dead-man switch | `pro/deadman.py:26-80` | `tests/test_pro_health_alerting.py:202-226` |
| Reconciliation + drift blocking | `execution/router.py:274-316`; `service.py:311-319` | `tests/test_pro_e2e_service.py`, `tests/test_pro_execution_router.py:100-240` |
| P3-08 self-assessment | `evals/self_assessment.py` | `tests/test_pro_evals_self_assessment.py` (:65-:136) |
| Credential redaction (incident fix) | `llm_clients/claude_cli_client.py` (commit `a5f8348`) | `tests/test_llm_claude_cli.py` (redaction pin) |
