#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# dev_run.sh — launch the RPC server in dev mode (stdin/stdout attached to
#              your terminal so you can hand-type JSON-RPC requests).
#
# Typical session:
#   $ scripts/dev_run.sh
#   {"jsonrpc":"2.0","id":1,"method":"app.ping"}
#   {"jsonrpc": "2.0", "id": 1, "result": "pong"}
#   {"jsonrpc":"2.0","id":2,"method":"workspace.status"}
#   ...
#   Ctrl-D to exit (or send app.shutdown).
# -----------------------------------------------------------------------------

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
    echo "No .venv Python found. Create one with:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[test,ai]'" >&2
    exit 1
fi

exec "${PYTHON}" -m dailystream.rpc_server
