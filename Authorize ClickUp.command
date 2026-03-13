#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "ClickUp OAuth authorization"
echo "Project: $ROOT_DIR"
echo

if [[ ! -f "./.env" ]]; then
  echo "Missing .env file in project root." >&2
  read -r -p "Press Enter to close..."
  exit 1
fi

if [[ ! -x "./.venv/bin/clickup-oauth" ]]; then
  echo "Missing OAuth helper in .venv. Installing project first..." >&2
  if [[ -x "./.venv/bin/pip" ]]; then
    "./.venv/bin/pip" install -e . >/dev/null
  else
    echo "Missing .venv or pip. Run Install Shipment Sync.command first." >&2
    read -r -p "Press Enter to close..."
    exit 1
  fi
fi

./.venv/bin/clickup-oauth || {
  echo
  echo "ClickUp OAuth setup failed. Check output above."
  read -r -p "Press Enter to close..."
  exit 1
}

echo
echo "ClickUp OAuth setup completed."
read -r -p "Press Enter to close..."
