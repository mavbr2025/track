#!/bin/bash
# ============================================================
# ArgusAI Daily Intelligence Run — MTM Logix
# Runs at 5:00 AM EST every day
# Opens Chrome to Lars Jensen's LinkedIn feed so Claude
# in Chrome can scrape posts and push to ClickUp
# ============================================================

LOG_FILE="$HOME/argusai_daily_run.log"
TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")

echo "[$TIMESTAMP] ArgusAI Daily Run starting..." >> "$LOG_FILE"

# ── 1. Make sure the screen is awake ──────────────────────
caffeinate -u -t 5 &

# ── 2. Open Chrome to Lars Jensen's LinkedIn activity feed ─
open -a "Google Chrome" "https://www.linkedin.com/in/larsjensenvespuccimaritime/recent-activity/all/"

echo "[$TIMESTAMP] Chrome opened to Lars Jensen LinkedIn feed." >> "$LOG_FILE"

# ── 3. Wait for Chrome to fully load (10 seconds) ─────────
sleep 10

# ── 4. Bring Chrome to the front ──────────────────────────
osascript <<'EOF'
tell application "Google Chrome"
    activate
end tell
EOF

echo "[$TIMESTAMP] Chrome activated and ready. Claude in Chrome shortcut should now be triggered." >> "$LOG_FILE"

# ── 5. Open the Claude in Chrome side panel ───────────────
# This uses Cmd+Shift+E (default Claude in Chrome shortcut)
osascript <<'EOF'
tell application "System Events"
    tell process "Google Chrome"
        keystroke "e" using {command down, shift down}
    end tell
end tell
EOF

sleep 3

echo "[$TIMESTAMP] Claude in Chrome panel opened." >> "$LOG_FILE"

# ── 6. Done — Claude in Chrome will detect the page and ───
#     the user's shortcut will fire the intelligence routine
echo "[$TIMESTAMP] ArgusAI Daily Run setup complete. Waiting for Claude to process." >> "$LOG_FILE"
