#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Setting up Shipment Sync on this Mac..."
echo "Project: $ROOT_DIR"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required but was not found." >&2
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

source ".venv/bin/activate"

echo "Installing dependencies..."
python -m pip install --upgrade pip
pip install -e ".[browser]"
python -m playwright install chrome

if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp ".env.example" ".env"
  echo
  echo "Created .env from .env.example."
  echo "Please fill .env credentials before running sync."
fi

if [[ -x "./scripts/install_shipment_launchd.sh" ]]; then
  echo
  echo "Installing scheduler (06:00 and 15:00 local time)..."
  ./scripts/install_shipment_launchd.sh
fi

echo
echo "Setup complete."
echo "To run manually:"
echo "./scripts/run_sync.sh"
