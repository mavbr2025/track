#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
LOCK_DIR="${LOCK_DIR:-$ROOT_DIR/.locks}"
LOCK_PATH="$LOCK_DIR/contacts-sync.lock"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-20}"

export PYTHONUNBUFFERED=1

load_env_without_overrides() {
  local env_file="$1"
  local raw key value trimmed_key

  [[ -f "$env_file" ]] || return 0

  while IFS= read -r raw || [[ -n "$raw" ]]; do
    [[ "$raw" =~ ^[[:space:]]*$ ]] && continue
    [[ "$raw" =~ ^[[:space:]]*# ]] && continue

    raw="${raw#export }"
    [[ "$raw" == *"="* ]] || continue

    key="${raw%%=*}"
    value="${raw#*=}"
    trimmed_key="$(printf '%s' "$key" | xargs)"
    [[ "$trimmed_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    if [[ -z "${!trimmed_key+x}" ]]; then
      if [[ "$value" =~ ^\".*\"$ ]] || [[ "$value" =~ ^\'.*\'$ ]]; then
        value="${value:1:${#value}-2}"
      fi
      export "$trimmed_key=$value"
    fi
  done <"$env_file"
}

run_once() {
  if [[ -x "$VENV_DIR/bin/contacts-sync" ]]; then
    "$VENV_DIR/bin/contacts-sync" "$@"
    return
  fi

  if command -v contacts-sync >/dev/null 2>&1; then
    contacts-sync "$@"
    return
  fi

  if [[ -x "$VENV_DIR/bin/python" ]]; then
    PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}" "$VENV_DIR/bin/python" -m shipment_sync.contacts_main "$@"
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}" python3 -m shipment_sync.contacts_main "$@"
    return
  fi

  echo "No Python runtime found. Install dependencies first." >&2
  return 1
}

load_env_without_overrides "$ROOT_DIR/.env"

required_vars=(
  CLICKUP_API_TOKEN
  CLICKUP_CONTACTS_LIST_ID
  ICLOUD_APPLE_ID
  ICLOUD_APP_SPECIFIC_PASSWORD
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required env var: $var_name" >&2
    echo "Expected file: $ROOT_DIR/.env" >&2
    exit 1
  fi
done

mkdir -p "$LOCK_DIR"
if ! mkdir "$LOCK_PATH" 2>/dev/null; then
  echo "Another contacts sync run is already in progress. Skipping this run."
  exit 0
fi
trap 'rmdir "$LOCK_PATH" >/dev/null 2>&1 || true' EXIT

attempt=1
while (( attempt <= MAX_ATTEMPTS )); do
  if run_once "$@"; then
    exit 0
  fi
  rc=$?
  if (( attempt >= MAX_ATTEMPTS )); then
    echo "Contacts sync failed after $attempt attempt(s)." >&2
    exit "$rc"
  fi
  echo "Contacts sync attempt $attempt failed (exit=$rc). Retrying in ${RETRY_DELAY_SECONDS}s..." >&2
  sleep "$RETRY_DELAY_SECONDS"
  attempt=$((attempt + 1))
done
