#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <bundle_url> [target_parent_dir]" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE_URL="$1"
TARGET_PARENT_DIR="${2:-$HOME/Backups}"
TMP_DIR="$(mktemp -d)"
TMP_BUNDLE="$TMP_DIR/shipment-backup.tar.gz"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "Downloading bundle..."
curl -fL "$BUNDLE_URL" -o "$TMP_BUNDLE"

"$ROOT_DIR/scripts/install_backup_bundle.sh" "$TMP_BUNDLE" "$TARGET_PARENT_DIR"
