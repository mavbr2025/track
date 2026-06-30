#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INITIAL_DELAY_SECONDS="${SHIPMENT_CRON_INITIAL_DELAY_SECONDS:-0}"
INTERVAL_SECONDS="${SHIPMENT_CRON_INTERVAL_SECONDS:-28800}"
RUN_TIMEOUT_SECONDS="${SHIPMENT_RUN_TIMEOUT_SECONDS:-5400}"
NETWORK_CHECK_HOSTS="${SHIPMENT_NETWORK_CHECK_HOSTS:-api.clickup.com}"
NETWORK_CHECK_ATTEMPTS="${SHIPMENT_NETWORK_CHECK_ATTEMPTS:-12}"
NETWORK_CHECK_DELAY_SECONDS="${SHIPMENT_NETWORK_CHECK_DELAY_SECONDS:-10}"

wait_for_network() {
  local hosts="$1"
  local attempts="$2"
  local delay_seconds="$3"

  if [[ -z "$hosts" || ! "$attempts" =~ ^[0-9]+$ || "$attempts" -eq 0 ]]; then
    return 0
  fi
  if [[ ! "$delay_seconds" =~ ^[0-9]+$ ]]; then
    delay_seconds=10
  fi

  local attempt host ok
  for (( attempt = 1; attempt <= attempts; attempt++ )); do
    ok=1
    IFS=',' read -ra host_array <<< "$hosts"
    for host in "${host_array[@]}"; do
      host="${host//[[:space:]]/}"
      [[ -z "$host" ]] && continue
      if ! getent hosts "$host" >/dev/null 2>&1; then
        ok=0
        break
      fi
    done
    if [[ "$ok" -eq 1 ]]; then
      return 0
    fi
    echo "Network/DNS preflight failed for ${host:-unknown}; attempt ${attempt}/${attempts}. Retrying in ${delay_seconds}s." >&2
    sleep "$delay_seconds"
  done

  echo "Network/DNS preflight did not recover after ${attempts} attempt(s); running sync so audit captures the failure." >&2
  return 0
}

if [[ "$INITIAL_DELAY_SECONDS" =~ ^[0-9]+$ ]] && (( INITIAL_DELAY_SECONDS > 0 )); then
  echo "Initial shipment sync delay: ${INITIAL_DELAY_SECONDS}s"
  sleep "$INITIAL_DELAY_SECONDS"
fi

while true; do
  started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "Starting shipment sync run at ${started_at}; timeout=${RUN_TIMEOUT_SECONDS}s"
  wait_for_network "$NETWORK_CHECK_HOSTS" "$NETWORK_CHECK_ATTEMPTS" "$NETWORK_CHECK_DELAY_SECONDS"

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
