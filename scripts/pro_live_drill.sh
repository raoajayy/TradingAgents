#!/usr/bin/env bash
# P3-01 kill-switch drill — Binance FUTURES **TESTNET ONLY**.
#
# What it does (in order, printing each step):
#   1. places one MINIMUM-SIZE entry with a reduce-only STOP_MARKET
#      protective stop in the same submission flow
#   2. verifies the stop is RESTING ON THE VENUE
#   3. engages the kill switch
#   4. cancels all resting orders + flattens all positions (reduce-only)
#   5. verifies the venue reports a flat book
#   6. disarms every pair and writes a drill record to the hash-chained
#      audit log
#
# Preconditions (all enforced again in code — this script only fails
# earlier with better messages):
#   - BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET exported
#   - the target pair armed at canary/live via `tradingagents-pro arm-live`
#   - PRO_DATA_DIR pointing at the deployment's /data volume
#
# Usage:
#   PRO_DATA_DIR=/data [SYMBOL=BTC-USD] ./scripts/pro_live_drill.sh
set -euo pipefail

PY="${PY:-$HOME/.venvs/tradingagents-pro/bin/python}"
SYMBOL="${SYMBOL:-BTC-USD}"
DATA_DIR="${PRO_DATA_DIR:?set PRO_DATA_DIR to the deployment data volume (e.g. /data)}"

: "${BINANCE_TESTNET_API_KEY:?export BINANCE_TESTNET_API_KEY (testnet.binancefuture.com API key)}"
: "${BINANCE_TESTNET_API_SECRET:?export BINANCE_TESTNET_API_SECRET}"

echo "=============================================================="
echo " P3-01 KILL-SWITCH DRILL — Binance FUTURES TESTNET"
echo "=============================================================="
echo " venue    : testnet.binancefuture.com (mainnet is refused in code)"
echo " symbol   : ${SYMBOL}"
echo " data dir : ${DATA_DIR}"
echo
echo " Steps: min-size entry -> verify venue stop resting -> engage"
echo " kill switch -> cancel+flatten all -> verify flat -> disarm all"
echo " -> audit record. A failed step still flattens and disarms."
echo

read -r -p "Type 'RUN DRILL' to place a real TESTNET order: " PHRASE
if [ "${PHRASE}" != "RUN DRILL" ]; then
  echo "aborted: confirmation phrase mismatch"
  exit 1
fi
read -r -p "Operator name (recorded in the audit log): " OPERATOR
if [ -z "${OPERATOR}" ]; then
  echo "aborted: operator identity is required"
  exit 1
fi

echo
echo "==> running drill as ${OPERATOR}"
exec "${PY}" -m tradingagents.pro.drill \
  --symbol "${SYMBOL}" \
  --operator "${OPERATOR}" \
  --data-dir "${DATA_DIR}" \
  --confirmed
