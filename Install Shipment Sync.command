#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Shipment Sync installer"
echo "Project folder: $ROOT_DIR"
echo

"$ROOT_DIR/scripts/setup_project_on_this_mac.sh" || {
  echo
  echo "Installer failed. Please check the errors above."
  echo
  read -r -p "Press Enter to close..."
  exit 1
}

echo
echo "Installer finished successfully."
read -r -p "Press Enter to close..."
