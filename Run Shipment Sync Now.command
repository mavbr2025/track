#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "Running Shipment Sync now..."
echo "Project: $ROOT_DIR"
echo

if [[ ! -x "./scripts/run_sync.sh" ]]; then
  echo "Missing run script: ./scripts/run_sync.sh" >&2
  read -r -p "Press Enter to close..."
  exit 1
fi

FORCE_DOTENV_OVERRIDES=true ./scripts/run_sync.sh || {
  echo
  echo "Sync failed. Check output above."
  read -r -p "Press Enter to close..."
  exit 1
}

echo
echo "Sync completed."
read -r -p "Press Enter to close..."
