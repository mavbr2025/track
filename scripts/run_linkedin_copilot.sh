#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"

export PYTHONUNBUFFERED=1

if [[ -x "$VENV_DIR/bin/linkedin-copilot" ]]; then
  exec "$VENV_DIR/bin/linkedin-copilot" "$@"
fi

if command -v linkedin-copilot >/dev/null 2>&1; then
  exec linkedin-copilot "$@"
fi

if [[ -x "$VENV_DIR/bin/python" ]]; then
  export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
  exec "$VENV_DIR/bin/python" -m shipment_sync.linkedin_copilot_main "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
  exec python3 -m shipment_sync.linkedin_copilot_main "$@"
fi

echo "No Python runtime found. Install dependencies first." >&2
exit 1
