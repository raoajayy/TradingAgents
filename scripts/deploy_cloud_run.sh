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
#
# Usage:
#   PROJECT_ID=my-project BUCKET=my-project-pro-data ./scripts/deploy_cloud_run.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to your GCP project id}"
: "${BUCKET:?Set BUCKET to the GCS bucket backing /data (see docs/DEPLOYMENT.md)}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-pro-dashboard}"
STAGING_SERVICE="${STAGING_SERVICE:-${SERVICE}-staging}"
STAGING_BUCKET="${STAGING_BUCKET:-${BUCKET}-staging}"
ARTIFACT_REPO="${ARTIFACT_REPO:-pro-dashboard}"
LLM_PROVIDER="${LLM_PROVIDER:-deepseek}"
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
LLM_KEY_ENV="${LLM_PROVIDER_UPPER}_API_KEY"

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
# Secrets: only the dashboard token + the LLM key are wired HERE. The
# Telegram alerting secrets live on the prod service out-of-band (applied
# once via gcloud/console; --update-secrets MERGES so redeploys keep them).
# Staging therefore never receives them — a fresh staging service carries
# exactly these two secrets, which is all the smoke check needs. Do NOT
# add prod alerting secrets to staging: a staging boot must not page
# anyone or message the prod Telegram channel.
deploy_service() {
  local service="$1" bucket="$2" loop_disabled="$3"
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
    --update-env-vars "TRADINGAGENTS_LLM_PROVIDER=${LLM_PROVIDER},PRO_LOOP_DISABLED=${loop_disabled},PRO_BACKTEST_STORE=firestore,LITESTREAM_REPLICA_URL=gcs://${bucket}/litestream" \
    --update-secrets "PRO_DASHBOARD_TOKEN=pro-dashboard-token:latest,${LLM_KEY_ENV}=${LLM_PROVIDER}-api-key:latest" \
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
