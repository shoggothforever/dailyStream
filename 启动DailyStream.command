#!/bin/bash
# 双击此文件即可启动 DailyStream 菜单栏应用
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_FW=$(ls /opt/homebrew/Cellar/python@*/*/Frameworks/Python.framework/Versions/*/Resources/Python.app/Contents/MacOS/Python 2>/dev/null | head -1)
SITE_PKG=$(ls -d "$SCRIPT_DIR/.venv/lib"/python*/site-packages 2>/dev/null | head -1)

export PYTHONPATH="$SCRIPT_DIR/src:$SITE_PKG"

echo "Starting DailyStream..."
echo "Python: $PYTHON_FW"
echo "PYTHONPATH: $PYTHONPATH"
echo ""

exec "$PYTHON_FW" -m dailystream.cli app
