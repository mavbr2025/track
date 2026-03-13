#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_SCRIPT="$ROOT_DIR/scripts/run_sync.sh"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/sync.log}"
CRON_TAG="# shipment-clickup-sync"
SCHEDULE="${1:-0 * * * *}"
ACTION="${2:-install}"

if [[ ! -x "$RUN_SCRIPT" ]]; then
  chmod +x "$RUN_SCRIPT"
fi

NEW_LINE="$SCHEDULE $RUN_SCRIPT >> $LOG_FILE 2>&1 $CRON_TAG"
CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
FILTERED_CRON="$(printf '%s\n' "$CURRENT_CRON" | awk -v tag="$CRON_TAG" 'index($0, tag) == 0')"

if [[ "$ACTION" == "remove" ]]; then
  printf '%s\n' "$FILTERED_CRON" | crontab -
  echo "Removed cron schedule tagged: $CRON_TAG"
  exit 0
fi

if [[ -n "$FILTERED_CRON" ]]; then
  printf '%s\n%s\n' "$FILTERED_CRON" "$NEW_LINE" | crontab -
else
  printf '%s\n' "$NEW_LINE" | crontab -
fi

echo "Installed cron schedule: $NEW_LINE"
