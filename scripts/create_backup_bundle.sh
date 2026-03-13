#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename "$ROOT_DIR")"
PARENT_DIR="$(dirname "$ROOT_DIR")"
OUTPUT_DIR="${1:-$HOME/Desktop}"
INCLUDE_ENV="${INCLUDE_ENV:-true}"
INCLUDE_ENV_NORMALIZED="$(printf '%s' "$INCLUDE_ENV" | tr '[:upper:]' '[:lower:]')"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BUNDLE_PATH="$OUTPUT_DIR/${PROJECT_NAME}-backup-${TIMESTAMP}.tar.gz"

mkdir -p "$OUTPUT_DIR"

EXCLUDES=(
  --exclude=".git"
  --exclude=".venv"
  --exclude="build"
  --exclude="artifacts/build"
  --exclude="artifacts/build_legacy_root"
  --exclude="artifacts/output_legacy_root"
  --exclude="artifacts/tmp"
  --exclude="__pycache__"
  --exclude="*.pyc"
  --exclude=".DS_Store"
  --exclude=".locks"
  --exclude="sync.log"
)

if [[ "$INCLUDE_ENV_NORMALIZED" != "true" ]]; then
  EXCLUDES+=(--exclude=".env")
fi

tar -czf "$BUNDLE_PATH" "${EXCLUDES[@]}" -C "$PARENT_DIR" "$PROJECT_NAME"

echo "Backup bundle created:"
echo "$BUNDLE_PATH"
echo
if [[ "$INCLUDE_ENV_NORMALIZED" == "true" ]]; then
  echo "Includes .env (contains credentials/secrets). Store and transfer securely."
else
  echo "Does not include .env. You must create .env on the target machine."
fi
