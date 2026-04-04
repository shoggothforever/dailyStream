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
    # Launch DailyStream menu bar app directly in current shell
    cd "$SCRIPT_DIR" && source .venv/bin/activate && PYTHONPATH=src python -c "
import os, sys, logging

import subprocess

import AppKit
ns_app = AppKit.NSApplication.sharedApplication()
ns_app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

# Set up logging so real errors go to file, not lost in noise
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.expanduser('~/.dailystream/app.log')),
    ]
)

from dailystream.app import DailyStreamApp
import traceback
try:
    app = DailyStreamApp()
    print('App created OK, calling run()...')
    app.run()
except Exception as e:
    traceback.print_exc()
" 2>&1 | grep -v 'TSM AdjustCapsLock\|IMKCFRunLoopWakeUp'
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
