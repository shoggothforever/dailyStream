#!/usr/bin/env bash
# DailyStream launcher script
# Usage:
#   ./run.sh [command] [args...]   — run CLI commands
#   ./run.sh install-service       — install as macOS LaunchAgent (auto-start on login)
#   ./run.sh uninstall-service     — remove LaunchAgent
#   ./run.sh start-service         — start the service now (without rebooting)
#   ./run.sh stop-service          — stop the service
#
# CLI examples:
#   ./run.sh start --path ~/notes
#   ./run.sh pipeline create reading
#   ./run.sh status
#   ./run.sh app

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
PLIST_SRC="$SCRIPT_DIR/com.dailystream.app.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.dailystream.app.plist"
LABEL="com.dailystream.app"

# Find venv site-packages
SITE_PACKAGES=$(ls -d "$SCRIPT_DIR/.venv/lib"/python*/site-packages 2>/dev/null | head -1)

# rumps requires Python.app framework (with Info.plist) to show menu bar icon
# The plain `python` binary cannot become a proper macOS UI process
PYTHON_FRAMEWORK=$(ls /opt/homebrew/Cellar/python@*/*/Frameworks/Python.framework/Versions/*/Resources/Python.app/Contents/MacOS/Python 2>/dev/null | head -1)
if [ -z "$PYTHON_FRAMEWORK" ]; then
    PYTHON_FRAMEWORK="$PYTHON"  # fallback
fi

export PYTHONPATH="$SCRIPT_DIR/src${SITE_PACKAGES:+:$SITE_PACKAGES}"

case "$1" in
  app)
    # Write launch script and open in Terminal.app for proper GUI session
    cat > /tmp/_ds_launch.sh << LAUNCH_EOF
#!/bin/bash
export PYTHONPATH="$SCRIPT_DIR/src:$SITE_PACKAGES"
exec "$PYTHON_FRAMEWORK" -m dailystream.cli app
LAUNCH_EOF
    chmod +x /tmp/_ds_launch.sh
    osascript -e 'tell application "Terminal" to do script "/tmp/_ds_launch.sh"'
    osascript -e 'tell application "Terminal" to activate'
    echo "✓ DailyStream launching in Terminal.app — check menu bar top-right"
    ;;
  install-service)
    cp "$PLIST_SRC" "$PLIST_DEST"
    launchctl load "$PLIST_DEST"
    echo "✓ DailyStream installed as LaunchAgent. It will start now and on every login."
    ;;
  uninstall-service)
    launchctl unload "$PLIST_DEST" 2>/dev/null
    rm -f "$PLIST_DEST"
    echo "✓ DailyStream LaunchAgent removed."
    ;;
  start-service)
    launchctl start "$LABEL"
    echo "✓ DailyStream started."
    ;;
  stop-service)
    launchctl stop "$LABEL"
    echo "✓ DailyStream stopped."
    ;;
  *)
    exec "$PYTHON" -m dailystream.cli "$@"
    ;;
esac
