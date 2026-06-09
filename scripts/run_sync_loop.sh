#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INITIAL_DELAY_SECONDS="${SHIPMENT_CRON_INITIAL_DELAY_SECONDS:-0}"
INTERVAL_SECONDS="${SHIPMENT_CRON_INTERVAL_SECONDS:-28800}"
RUN_TIMEOUT_SECONDS="${SHIPMENT_RUN_TIMEOUT_SECONDS:-5400}"

if [[ "$INITIAL_DELAY_SECONDS" =~ ^[0-9]+$ ]] && (( INITIAL_DELAY_SECONDS > 0 )); then
  echo "Initial shipment sync delay: ${INITIAL_DELAY_SECONDS}s"
  sleep "$INITIAL_DELAY_SECONDS"
fi

while true; do
  started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "Starting shipment sync run at ${started_at}; timeout=${RUN_TIMEOUT_SECONDS}s"

  if timeout --preserve-status "$RUN_TIMEOUT_SECONDS" "$ROOT_DIR/scripts/run_sync.sh"; then
    echo "Shipment sync run completed at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  else
    status=$?
    if [[ "$status" -eq 124 || "$status" -eq 137 ]]; then
      echo "Shipment sync run timed out after ${RUN_TIMEOUT_SECONDS}s at $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >&2
    else
      echo "Shipment sync run exited with status ${status} at $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >&2
    fi
  fi

  echo "Sleeping ${INTERVAL_SECONDS}s before next shipment sync run"
  sleep "$INTERVAL_SECONDS"
done
