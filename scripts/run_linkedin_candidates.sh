#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"

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

load_env_without_overrides "$ROOT_DIR/.env"

has_flag() {
  local wanted="$1"
  shift
  for arg in "$@"; do
    if [[ "$arg" == "$wanted" ]]; then
      return 0
    fi
  done
  return 1
}

has_option_value() {
  local wanted="$1"
  shift
  local expecting_value=false
  for arg in "$@"; do
    if [[ "$expecting_value" == "true" ]]; then
      if [[ -n "$arg" && "$arg" != --* ]]; then
        return 0
      fi
      expecting_value=false
    fi

    if [[ "$arg" == "$wanted" ]]; then
      expecting_value=true
      continue
    fi

    if [[ "$arg" == "$wanted="* ]]; then
      local value="${arg#*=}"
      if [[ -n "$value" ]]; then
        return 0
      fi
    fi
  done
  return 1
}

skip_validation=false
if has_flag "--help" "$@" || has_flag "-h" "$@"; then
  skip_validation=true
fi

csv_mode=false
if has_option_value "--input-csv" "$@"; then
  csv_mode=true
fi

require_clickup=true
if has_flag "--dry-run" "$@"; then
  require_clickup=false
fi
if has_flag "--inspect-fields" "$@"; then
  require_clickup=true
fi

if [[ "$skip_validation" != "true" && "$csv_mode" != "true" ]]; then
  if [[ -z "${GOOGLE_CSE_API_KEY:-}" ]]; then
    echo "Missing required env var: GOOGLE_CSE_API_KEY" >&2
    exit 1
  fi
  if [[ -z "${GOOGLE_CSE_ENGINE_ID:-}" ]]; then
    echo "Missing required env var: GOOGLE_CSE_ENGINE_ID" >&2
    exit 1
  fi
fi

if [[ "$skip_validation" != "true" && "$require_clickup" == "true" ]]; then
  required_vars=(
    CLICKUP_API_TOKEN
    CLICKUP_CANDIDATES_LIST_ID
  )
  for var_name in "${required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
      echo "Missing required env var: $var_name" >&2
      echo "Expected file: $ROOT_DIR/.env" >&2
      exit 1
    fi
  done
fi

if [[ -x "$VENV_DIR/bin/linkedin-candidates" ]]; then
  exec "$VENV_DIR/bin/linkedin-candidates" "$@"
fi

if command -v linkedin-candidates >/dev/null 2>&1; then
  exec linkedin-candidates "$@"
fi

if [[ -x "$VENV_DIR/bin/python" ]]; then
  export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
  exec "$VENV_DIR/bin/python" -m shipment_sync.linkedin_candidates_main "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
  exec python3 -m shipment_sync.linkedin_candidates_main "$@"
fi

echo "No Python runtime found. Install dependencies first." >&2
exit 1
