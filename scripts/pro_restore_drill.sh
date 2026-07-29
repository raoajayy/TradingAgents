#!/usr/bin/env bash
# P2-01 restore drill: prove the bucket replica reconstructs the event
# store. Run from any machine with gcloud ADC + litestream installed
# (brew install litestream). Simulates "container deleted": restores the
# replica to a scratch path and exports it to JSONL so you can eyeball /
# diff counts against the live dashboard.
#
#   BUCKET=trading-agent-pro-c3dc6-pro-data ./scripts/pro_restore_drill.sh
#
# Gotchas found running the first drill (2026-07-29, PASSED: 322 runs /
# 617 memory rows / prefs, restored purely from the bucket):
# - use litestream v0.3.x — prod writes v0.3 replica format; v0.5.x
#   cannot read it (and renamed the URL scheme gcs:// -> gs://).
# - litestream needs ADC (gcloud auth application-default login). If you
#   only have CLI auth: `gcloud storage cp -r gs://$BUCKET/litestream .`
#   then restore from "file://$PWD/litestream" instead.
set -euo pipefail

BUCKET="${BUCKET:?set BUCKET (GCS bucket name)}"
REPLICA="gcs://${BUCKET}/litestream"
SCRATCH="$(mktemp -d)"
DB="${SCRATCH}/restored.db"

echo "==> restoring ${REPLICA} -> ${DB}"
litestream restore -o "${DB}" "${REPLICA}"

echo "==> exporting restored store to ${SCRATCH}/export"
TRADINGAGENTS_PRO_DB="${DB}" python -m tradingagents.pro.store export \
  --out "${SCRATCH}/export"

echo "==> restored contents:"
for f in "${SCRATCH}"/export/*; do
  printf "  %-28s %s lines\n" "$(basename "$f")" "$(wc -l < "$f" | tr -d ' ')"
done
echo "==> drill complete. Compare counts with /api/runs on the live site."
echo "    scratch dir kept at ${SCRATCH} (delete when done)"
