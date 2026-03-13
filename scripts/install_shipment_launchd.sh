#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_SCRIPT="$ROOT_DIR/scripts/run_sync.sh"
LABEL="${LABEL:-com.mtm.shipment.clickup.sync}"
ACTION="${1:-install}"

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
PLIST_PATH="$LAUNCH_AGENTS_DIR/${LABEL}.plist"
OUT_LOG="${OUT_LOG:-$LOG_DIR/shipment-clickup-sync.log}"
ERR_LOG="${ERR_LOG:-$LOG_DIR/shipment-clickup-sync.err.log}"

if [[ ! -x "$RUN_SCRIPT" ]]; then
  chmod +x "$RUN_SCRIPT"
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

if [[ "$ACTION" == "remove" ]]; then
  launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
  rm -f "$PLIST_PATH"
  echo "Removed LaunchAgent: $LABEL"
  echo "Plist removed: $PLIST_PATH"
  exit 0
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Missing required env file: $ROOT_DIR/.env" >&2
  exit 1
fi

cat >"$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
      <string>${RUN_SCRIPT}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${ROOT_DIR}</string>

    <key>StartCalendarInterval</key>
    <array>
      <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>0</integer>
      </dict>
      <dict>
        <key>Hour</key>
        <integer>15</integer>
        <key>Minute</key>
        <integer>0</integer>
      </dict>
    </array>

    <key>StandardOutPath</key>
    <string>${OUT_LOG}</string>

    <key>StandardErrorPath</key>
    <string>${ERR_LOG}</string>
  </dict>
</plist>
EOF

launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$PLIST_PATH"
launchctl enable "gui/$UID/$LABEL"
launchctl kickstart -k "gui/$UID/$LABEL"

echo "Installed LaunchAgent: $LABEL"
echo "Schedule: daily at 06:00 and 15:00 (macOS local timezone)"
echo "Plist: $PLIST_PATH"
echo "Logs: $OUT_LOG"
echo "Errors: $ERR_LOG"
echo
echo "For Mexico time, ensure macOS timezone is set to America/Mexico_City."
