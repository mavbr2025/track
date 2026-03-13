#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <bundle.tar.gz> [target_parent_dir]" >&2
  exit 1
fi

BUNDLE_PATH="$1"
TARGET_PARENT_DIR="${2:-$HOME/Backups}"
INSTALL_SCHEDULER="${INSTALL_SCHEDULER:-true}"

if [[ ! -f "$BUNDLE_PATH" ]]; then
  echo "Bundle not found: $BUNDLE_PATH" >&2
  exit 1
fi

mkdir -p "$TARGET_PARENT_DIR"

TOP_ENTRY="$(tar -tzf "$BUNDLE_PATH" | head -n 1)"
TOP_DIR="${TOP_ENTRY%%/*}"
if [[ -z "$TOP_DIR" ]]; then
  echo "Could not detect project folder inside bundle." >&2
  exit 1
fi

tar -xzf "$BUNDLE_PATH" -C "$TARGET_PARENT_DIR"
PROJECT_DIR="$TARGET_PARENT_DIR/$TOP_DIR"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Extracted project directory not found: $PROJECT_DIR" >&2
  exit 1
fi

cd "$PROJECT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi

source ".venv/bin/activate"
pip install --upgrade pip
pip install -e ".[browser]"
python -m playwright install chrome

if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp ".env.example" ".env"
  echo "Created .env from .env.example. Fill credentials before running sync."
fi

if [[ "$INSTALL_SCHEDULER" == "true" && -x "./scripts/install_shipment_launchd.sh" ]]; then
  ./scripts/install_shipment_launchd.sh
fi

echo
echo "Backup installed at:"
echo "$PROJECT_DIR"
echo
echo "Run manually:"
echo "cd \"$PROJECT_DIR\" && source .venv/bin/activate && ./scripts/run_sync.sh"
