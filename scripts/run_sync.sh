#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"

# Make Python output stream immediately so long runs show progress in real time.
export PYTHONUNBUFFERED=1
FORCE_DOTENV_OVERRIDES="${FORCE_DOTENV_OVERRIDES:-false}"

load_env() {
  local env_file="$1"
  local raw key value trimmed_key

  [[ -f "$env_file" ]] || return 0

  while IFS= read -r raw || [[ -n "$raw" ]]; do
    # Skip blanks/comments.
    [[ "$raw" =~ ^[[:space:]]*$ ]] && continue
    [[ "$raw" =~ ^[[:space:]]*# ]] && continue

    # Support optional "export KEY=VALUE" form.
    raw="${raw#export }"
    [[ "$raw" == *"="* ]] || continue

    key="${raw%%=*}"
    value="${raw#*=}"
    # Trim spaces around key.
    trimmed_key="$(printf '%s' "$key" | xargs)"
    [[ "$trimmed_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    # Strip matching surrounding quotes.
    if [[ "$value" =~ ^\".*\"$ ]] || [[ "$value" =~ ^\'.*\'$ ]]; then
      value="${value:1:${#value}-2}"
    fi

    # Keep explicit CLI/env overrides unless launcher forces .env precedence.
    if [[ "$FORCE_DOTENV_OVERRIDES" == "true" ]] || [[ -z "${!trimmed_key+x}" ]]; then
      export "$trimmed_key=$value"
    fi
  done <"$env_file"
}

load_env "$ROOT_DIR/.env"

required_vars=(
  CLICKUP_LIST_ID
  CLICKUP_CF_CONTAINER_NO
  CLICKUP_CF_BOOKING_NO
  CLICKUP_CF_SHIPPING_LINE
)

if [[ -z "${CLICKUP_OAUTH_ACCESS_TOKEN:-}" && -z "${CLICKUP_API_TOKEN:-}" ]]; then
  echo "Missing ClickUp credentials. Set CLICKUP_OAUTH_ACCESS_TOKEN or CLICKUP_API_TOKEN." >&2
  echo "Expected file: $ROOT_DIR/.env" >&2
  exit 1
fi

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required env var: $var_name" >&2
    echo "Expected file: $ROOT_DIR/.env" >&2
    exit 1
  fi
done

if [[ -x "$VENV_DIR/bin/shipment-sync" ]]; then
  exec "$VENV_DIR/bin/shipment-sync" "$@"
fi

if command -v shipment-sync >/dev/null 2>&1; then
  exec shipment-sync "$@"
fi

if [[ -x "$VENV_DIR/bin/python" ]]; then
  export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
  exec "$VENV_DIR/bin/python" -m shipment_sync.main "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
  exec python3 -m shipment_sync.main "$@"
fi

echo "No Python runtime found. Install dependencies first." >&2
exit 1
