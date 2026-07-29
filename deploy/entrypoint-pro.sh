#!/bin/sh
# P2-01: boot with Litestream when a replica URL is configured.
#
# The SQLite event store must live on LOCAL disk (TRADINGAGENTS_PRO_DB,
# default /tmp/pro.db in the image) — the GCS FUSE mount at /data cannot
# hold SQLite locks. Durability comes from Litestream: restore the DB
# from the bucket if the local copy is missing (fresh container), then
# replicate continuously while the app runs. Without a replica URL
# (local docker, tests) the app just runs; the DB then lives wherever
# TRADINGAGENTS_PRO_DB points.
set -eu

DB_PATH="${TRADINGAGENTS_PRO_DB:-/tmp/pro.db}"

if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
  if [ ! -f "$DB_PATH" ]; then
    echo "entrypoint: restoring ${DB_PATH} from ${LITESTREAM_REPLICA_URL}"
    litestream restore -if-replica-exists -o "$DB_PATH" \
      "$LITESTREAM_REPLICA_URL" || echo "entrypoint: no replica yet (first boot)"
  fi
  echo "entrypoint: replicating ${DB_PATH} -> ${LITESTREAM_REPLICA_URL}"
  exec litestream replicate -exec "python -m tradingagents.pro.main" \
    "$DB_PATH" "$LITESTREAM_REPLICA_URL"
fi

exec python -m tradingagents.pro.main
