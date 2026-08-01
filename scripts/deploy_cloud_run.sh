#!/usr/bin/env bash
# Build + deploy the Pro dashboard (deploy/Dockerfile.pro) to Cloud Run, for
# use behind Firebase Hosting (see firebase.json + docs/DEPLOYMENT.md).
#
# Deploy flow (P2-08): staging -> smoke -> prod. The image is built once,
# deployed first to ${SERVICE}-staging (its own GCS bucket "${BUCKET}-staging"
# for the /data volume and its own Litestream replica path, hourly loop
# always disabled), smoke-checked over HTTP, and only then promoted to the
# prod service. A failing smoke check aborts before prod is touched.
#
# One-time GCP setup (APIs, Artifact Registry repo, GCS bucket, secrets) is
# NOT done by this script — see the "Cloud Run + Firebase Hosting" section
# of docs/DEPLOYMENT.md for that checklist. This script only builds the
# image and (re)deploys the Cloud Run services; safe to re-run. The staging
# bucket ("${BUCKET}-staging" by default) must exist, same as the prod one.
#
# Required env vars:
#   PROJECT_ID        GCP project id (the one backing your Firebase project)
#   BUCKET            GCS bucket name backing the /data volume (persistence)
# Optional (defaults shown):
#   REGION=asia-south1
#   SERVICE=pro-dashboard
#   STAGING_SERVICE=${SERVICE}-staging
#   STAGING_BUCKET=${BUCKET}-staging
#   ARTIFACT_REPO=pro-dashboard
#   LLM_PROVIDER=deepseek
#   PRO_LOOP_DISABLED=0   # the hourly paper loop RUNS by default (accrual
#                         # clock, roadmap workstream T); set 1 to deploy
#                         # dashboard-only with on-demand runs. Applies to
#                         # PROD only — staging is ALWAYS deployed with
#                         # PRO_LOOP_DISABLED=1 (no second accrual clock,
#                         # no duplicate paper orders).
#   SKIP_STAGING=0        # set 1 to bypass the staging deploy + smoke gate.
#                         # Escape hatch ONLY: e.g. shipping an urgent prod
#                         # fix while the staging service/bucket is itself
#                         # broken or being re-provisioned. Every routine
#                         # deploy must go through staging.
#   TRADINGAGENTS_QUICK_THINK_LLM / TRADINGAGENTS_DEEP_THINK_LLM
#                         # optional model overrides; when set they are passed
#                         # through to the service via --update-env-vars
#                         # (otherwise the service keeps its current values /
#                         # the code defaults).
#
# Service-side tuning env vars (NOT set by this script — apply out-of-band
# with `gcloud run services update --update-env-vars`; --update-env-vars
# MERGES, so redeploys keep them):
#   PRO_MAX_PORTFOLIO_VAR_PCT     P2-05 cap on parametric 1-day 99% portfolio
#                                 VaR as a percent of equity, checked
#                                 pre-trade. Unset = disabled (contract
#                                 default).
#   PRO_MAX_CORRELATED_GROSS_PCT  P2-05 cap on gross exposure (percent of
#                                 equity) of the candidate plus correlated
#                                 (|corr| > 0.6) open positions. Unset =
#                                 disabled.
#   PRO_MAX_RUNS                  recorder boot-RAM knob: how many recorded
#                                 runs (each holds a full snapshot) are kept
#                                 in memory / reloaded at boot. Default 500 —
#                                 this script deliberately does not set it.
#   PRO_MAX_VOL_INTERVAL_WIDTH_PCT
#                                 P3-04 conformal vol-uncertainty gate: cap
#                                 on the adaptive-conformal interval width
#                                 around the HAR vol forecast, percent of
#                                 price. Unset = gate disabled (contract
#                                 default).
#   PRO_VOL_INTERVAL_SIZE_SCALE   P3-04: set 1 so a width breach scales the
#                                 position by cap/width (floor 0.25) instead
#                                 of blocking the entry. Unset = block.
#
# Optional operator-supplied secrets (create + wire out-of-band, exactly like
# the Telegram alerting secrets — this script does NOT manage them):
#   OANDA_API_TOKEN               enables OANDA intraday FX bars (EURUSD /
#                                 USDJPY at 1h/4h); without it FX falls back
#                                 to yfinance DAILY bars and intraday FX
#                                 pipeline runs are refused with a 422.
#   TOKENTERMINAL_API_KEY         enables the Token Terminal fundamentals
#                                 feed on crypto snapshots (P1-05d).
#
# Usage:
#   PROJECT_ID=my-project BUCKET=my-project-pro-data ./scripts/deploy_cloud_run.sh
#
# claude-cli provider (subscription-billed via a headless OAuth token):
#   The LLM-key convention maps claude-cli -> secret "claude-cli-api-key"
#   wired to the CLAUDE_CODE_OAUTH_TOKEN env var (see api_key_env.py).
#   1. Mint a long-lived token on your own machine with `claude setup-token`,
#      then create the secret by pasting it YOURSELF — the token is operator-
#      typed, never scripted or committed:
#        printf '%s' '<token from claude setup-token>' | \
#          gcloud secrets create claude-cli-api-key --data-file=-
#   2. Deploy with the provider + model overrides (the defaults are OpenAI
#      model IDs, which the claude CLI does not serve):
#        PROJECT_ID=my-project BUCKET=my-project-pro-data \
#        LLM_PROVIDER=claude-cli \
#        TRADINGAGENTS_QUICK_THINK_LLM=claude-haiku-4-5-20251001 \
#        TRADINGAGENTS_DEEP_THINK_LLM=claude-sonnet-5 \
#        ./scripts/deploy_cloud_run.sh
#   Caveats (rate limits, token revocation, why a metered API key stays the
#   recommended primary for 24/7 loops): docs/DEPLOYMENT.md,
#   section "claude-cli in production".
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to your GCP project id}"
: "${BUCKET:?Set BUCKET to the GCS bucket backing /data (see docs/DEPLOYMENT.md)}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-pro-dashboard}"
STAGING_SERVICE="${STAGING_SERVICE:-${SERVICE}-staging}"
STAGING_BUCKET="${STAGING_BUCKET:-${BUCKET}-staging}"
ARTIFACT_REPO="${ARTIFACT_REPO:-pro-dashboard}"
LLM_PROVIDER="${LLM_PROVIDER:-deepseek}"
# P3-07 provenance: stamp the deployed revision with the deploying commit
# so prod version stamps carry a real git_sha, not "unknown" (P3-12 gap)
GIT_SHA_VALUE="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
SKIP_STAGING="${SKIP_STAGING:-0}"

# curl + python3 drive the smoke check between the staging and prod deploys
for bin in gcloud git curl python3; do
  command -v "$bin" >/dev/null || { echo "missing required tool: $bin" >&2; exit 1; }
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG="$(git rev-parse --short HEAD)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/${SERVICE}:${TAG}"
# portable uppercase (macOS ships bash 3.2 — no ${VAR^^} support there)
LLM_PROVIDER_UPPER="$(printf '%s' "$LLM_PROVIDER" | tr '[:lower:]' '[:upper:]')"
if [ "$LLM_PROVIDER" = "claude-cli" ]; then
  # the <PROVIDER>_API_KEY convention would yield "CLAUDE-CLI_API_KEY" —
  # not a legal env-var name. claude-cli's key env is the headless OAuth
  # token (single source of truth: tradingagents/llm_clients/api_key_env.py).
  # The SECRET name stays on the convention: "claude-cli-api-key" (gcloud
  # secret names allow hyphens).
  LLM_KEY_ENV="CLAUDE_CODE_OAUTH_TOKEN"
else
  LLM_KEY_ENV="${LLM_PROVIDER_UPPER}_API_KEY"
fi

echo "==> Building + pushing ${IMAGE} via Cloud Build"
# `gcloud builds submit --tag` always runs `docker build -t $TAG .` with the
# default Dockerfile path — it can't take -f alongside --tag — so use the
# explicit build config instead (deploy/cloudbuild.yaml).
gcloud builds submit \
  --project "$PROJECT_ID" \
  --config deploy/cloudbuild.yaml \
  --substitutions "_IMAGE=${IMAGE}" \
  .

# deploy_service SERVICE BUCKET LOOP_DISABLED — one Cloud Run (re)deploy.
#
# --max-instances=1 keeps a single writer. Since P2-01 this is ADVISORY
# for runs/memory/prefs (they live in the SQLite event store on local
# disk, Litestream-replicated to gs://$BUCKET/litestream — a second
# instance would fork replication history, so keep 1 until leases exist)
# and still REQUIRED for the /data whole-file writers (hash-chained audit
# log, arming state, paper book). --min-instances=1 keeps that singleton
# WARM: with scale-to-zero, every cold boot briefly served "monitor only"
# safety chrome before the paper service attached, so equity/status chips
# flickered between page loads (trader review P0.4 — a control surface
# whose LIVE state depends on which boot served you reads as broken).
# --execution-environment gen2 is required for the Cloud Storage volume mount.
# --update-env-vars/--update-secrets (not --set-*) MERGE with what's already
# on the service, so redeploys never wipe env applied out-of-band (e.g. the
# Google sign-in vars PRO_FIREBASE_PROJECT_ID/PRO_ALLOWED_EMAILS/
# PRO_FIREBASE_WEB_CONFIG — see docs/DEPLOYMENT.md).
#
# Secrets: the dashboard token, the LLM key, and the FRED key are wired
# HERE. The Telegram alerting secrets live on the prod service out-of-band
# (applied once via gcloud/console; --update-secrets MERGES so redeploys
# keep them). Staging therefore never receives those — a fresh staging
# service carries exactly the three secrets below, which is all the smoke
# check needs. Do NOT add prod alerting secrets to staging: a staging boot
# must not page anyone or message the prod Telegram channel.
#
# fred-api-key (P3-02): wired unconditionally to FRED_API_KEY — the macro
# snapshot feed AND the vintage capture (point-in-time metric history)
# depend on it. The secret must already exist in Secret Manager (it does on
# this project; see the one-time setup checklist in docs/DEPLOYMENT.md):
#   printf '%s' '<key>' | gcloud secrets create fred-api-key --data-file=-
# gcloud cannot conditionally include a secret in one deploy command, so a
# missing secret fails the deploy loudly instead of silently shipping a
# service without macro vintages.
deploy_service() {
  local service="$1" bucket="$2" loop_disabled="$3" env_vars
  # PRO_EVENT_TRIGGERS=1 turns on P2-06 event-driven runs (calendar T+5min /
  # vol spike / price gap). It only takes effect when the hourly loop is
  # enabled (main.py starts the trigger daemon inside the loop branch), so
  # staging — always deployed loop-disabled — stays inert with it set.
  env_vars="TRADINGAGENTS_LLM_PROVIDER=${LLM_PROVIDER},PRO_LOOP_DISABLED=${loop_disabled},PRO_EVENT_TRIGGERS=1,PRO_BACKTEST_STORE=firestore,LITESTREAM_REPLICA_URL=gcs://${bucket}/litestream,GIT_SHA=${GIT_SHA_VALUE}"
  # optional model overrides: forwarded only when set, so redeploys without
  # them keep whatever is already on the service (--update-env-vars merges)
  if [ -n "${TRADINGAGENTS_QUICK_THINK_LLM:-}" ]; then
    env_vars="${env_vars},TRADINGAGENTS_QUICK_THINK_LLM=${TRADINGAGENTS_QUICK_THINK_LLM}"
  fi
  if [ -n "${TRADINGAGENTS_DEEP_THINK_LLM:-}" ]; then
    env_vars="${env_vars},TRADINGAGENTS_DEEP_THINK_LLM=${TRADINGAGENTS_DEEP_THINK_LLM}"
  fi
  echo "==> Deploying ${service} to Cloud Run (${REGION}) [bucket=${bucket}, loop_disabled=${loop_disabled}]"
  gcloud run deploy "$service" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --image "$IMAGE" \
    --execution-environment gen2 \
    --min-instances 1 \
    --max-instances 1 \
    --no-cpu-throttling \
    --memory 1Gi \
    --concurrency 250 \
    --add-volume "name=data,type=cloud-storage,bucket=${bucket}" \
    --add-volume-mount "volume=data,mount-path=/data" \
    --update-env-vars "$env_vars" \
    --update-secrets "PRO_DASHBOARD_TOKEN=pro-dashboard-token:latest,${LLM_KEY_ENV}=${LLM_PROVIDER}-api-key:latest,FRED_API_KEY=fred-api-key:latest" \
    --allow-unauthenticated
}

service_url() {
  gcloud run services describe "$1" \
    --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)'
}

# health_feeds_only FILE — exit 0 iff the /health/live JSON in FILE is either
# ok, or degraded ONLY by the "feeds" check. Staging runs without live venue
# keys and with the loop disabled, so missing/stale feeds are expected there;
# any OTHER failing check (venue, clock, kill_switch, run_recency) means the
# build itself is unhealthy and must not be promoted.
health_feeds_only() {
  python3 - "$1" <<'PY'
import json
import sys

try:
    report = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
if report.get("ok"):
    sys.exit(0)
failing = [c.get("name") for c in report.get("checks", []) if not c.get("ok")]
sys.exit(0 if failing and set(failing) <= {"feeds"} else 1)
PY
}

# smoke_check BASE_URL — gate between staging and prod:
#   /health/live       HTTP 200, or 503 whose only failing check is "feeds"
#   /api/auth/config   HTTP 200 (open route; proves the app + auth config boot)
smoke_check() {
  local url="$1" body code attempt
  body="$(mktemp)"
  echo "==> Smoke-checking ${url}"
  for attempt in $(seq 1 12); do
    code="$(curl -sS -o "$body" -w '%{http_code}' --max-time 20 \
      "${url}/health/live" || echo 000)"
    if [ "$code" = "200" ] || { [ "$code" = "503" ] && health_feeds_only "$body"; }; then
      echo "    /health/live -> ${code} (pass)"
      code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 \
        "${url}/api/auth/config" || echo 000)"
      if [ "$code" = "200" ]; then
        echo "    /api/auth/config -> 200 (pass)"
        rm -f "$body"
        return 0
      fi
      echo "    /api/auth/config -> ${code} (want 200)" >&2
    else
      echo "    attempt ${attempt}/12: /health/live -> ${code} (not passing yet)"
    fi
    [ "$attempt" -lt 12 ] && sleep 10
  done
  echo "==> SMOKE CHECK FAILED for ${url}; last /health/live body:" >&2
  cat "$body" >&2 || true
  echo >&2
  rm -f "$body"
  return 1
}

if [ "$SKIP_STAGING" = "1" ]; then
  echo "==> SKIP_STAGING=1 — bypassing the staging deploy + smoke gate (emergency use only)"
else
  # staging always runs loop-disabled: one accrual clock, one paper book —
  # the staging instance must never place duplicate paper orders.
  deploy_service "$STAGING_SERVICE" "$STAGING_BUCKET" 1
  STAGING_URL="$(service_url "$STAGING_SERVICE")"
  if ! smoke_check "$STAGING_URL"; then
    echo "==> Staging smoke check failed — prod deploy ABORTED" >&2
    exit 1
  fi
  echo "==> Staging healthy — promoting ${IMAGE} to ${SERVICE}"
fi

deploy_service "$SERVICE" "$BUCKET" "${PRO_LOOP_DISABLED:-0}"

echo "==> Done. Fetch the Cloud Run URL with:"
echo "    gcloud run services describe ${SERVICE} --project ${PROJECT_ID} --region ${REGION} --format='value(status.url)'"
echo "==> Then point firebase.json's hosting rewrite at serviceId=${SERVICE}, region=${REGION}, and run:"
echo "    firebase deploy --only hosting"
