#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="${LABEL:-com.mtm.clickup.icloud.contacts.sync}"
INTERVAL_SECONDS="${1:-300}"
ACTION="${2:-install}"
RUNTIME_DIR="${RUNTIME_DIR:-$HOME/.local/share/clickup-icloud-contacts-sync}"
RUNTIME_SCRIPTS_DIR="$RUNTIME_DIR/scripts"
RUNTIME_RUN_SCRIPT="$RUNTIME_SCRIPTS_DIR/run_contacts_sync.sh"
RUNTIME_ENV="$RUNTIME_DIR/.env"
RUNTIME_VENV="$RUNTIME_DIR/.venv"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"
PLIST_PATH="$LAUNCH_AGENTS_DIR/${LABEL}.plist"
OUT_LOG="${OUT_LOG:-$LOG_DIR/clickup-icloud-contacts-sync.log}"
ERR_LOG="${ERR_LOG:-$LOG_DIR/clickup-icloud-contacts-sync.err.log}"
SOURCE_RUN_SCRIPT="$ROOT_DIR/scripts/run_contacts_sync.sh"
SOURCE_ENV="$ROOT_DIR/.env"

if [[ ! "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || (( INTERVAL_SECONDS < 60 )); then
  echo "INTERVAL_SECONDS must be an integer >= 60." >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"
chmod +x "$SOURCE_RUN_SCRIPT"

if [[ "$ACTION" == "remove" ]]; then
  launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
  rm -f "$PLIST_PATH"
  echo "Removed LaunchAgent: $LABEL"
  echo "Plist removed: $PLIST_PATH"
  exit 0
fi

if [[ ! -f "$SOURCE_ENV" ]]; then
  echo "Missing required env file: $SOURCE_ENV" >&2
  exit 1
fi

mkdir -p "$RUNTIME_SCRIPTS_DIR"
cp "$SOURCE_RUN_SCRIPT" "$RUNTIME_RUN_SCRIPT"
chmod +x "$RUNTIME_RUN_SCRIPT"
cp "$SOURCE_ENV" "$RUNTIME_ENV"
chmod 600 "$RUNTIME_ENV"

if [[ ! -x "$RUNTIME_VENV/bin/python" ]]; then
  python3 -m venv "$RUNTIME_VENV"
fi

"$RUNTIME_VENV/bin/pip" install --upgrade "$ROOT_DIR" >/dev/null

cat >"$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
      <string>${RUNTIME_RUN_SCRIPT}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>StartInterval</key>
    <integer>${INTERVAL_SECONDS}</integer>

    <key>WorkingDirectory</key>
    <string>${RUNTIME_DIR}</string>

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
echo "Interval: every ${INTERVAL_SECONDS} seconds"
echo "Plist: $PLIST_PATH"
echo "Runtime dir: $RUNTIME_DIR"
echo "Logs: $OUT_LOG"
echo "Errors: $ERR_LOG"
