#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# sign_python_framework.sh — codesign every native binary inside the embedded
#                            Python.framework (placeholder for M5).
#
# Usage
#   scripts/sign_python_framework.sh "Developer ID Application: Your Name (TEAMID)"
#
# What it does
#   Recursively finds every Mach-O binary under the framework
#   (executables + .dylib + .so) and codesigns each with the
#   hardened runtime entitlement.  This is required before notary
#   submission; without it notarytool rejects the .app.
#
# Notes (省心模式 — deferred to M5)
#   * M0-M4 use ad-hoc signing (the system default) and do not call
#     this script.  The Swift shell runs locally just fine without it.
#   * In M5 we hook this into scripts/build_release.sh.  The body is
#     filled in there; this file is intentionally a stub so the
#     release flow is discoverable now.
# -----------------------------------------------------------------------------

set -euo pipefail

IDENTITY="${1:-}"
if [[ -z "${IDENTITY}" ]]; then
    cat >&2 <<EOF
usage: $(basename "$0") "Developer ID Application: ... (TEAMID)"

This script is a placeholder for M5.  Signing is skipped in 省心模式.
When you are ready to ship, wire this into scripts/build_release.sh and
provide a real Developer ID identity.
EOF
    exit 64
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRAMEWORK="${REPO_ROOT}/apps/DailyStreamMac/Frameworks/Python.framework"

if [[ ! -d "${FRAMEWORK}" ]]; then
    echo "Framework not found at ${FRAMEWORK}" >&2
    echo "Run scripts/bundle_python.sh first." >&2
    exit 1
fi

# Placeholder iteration — uncomment in M5.
# find "${FRAMEWORK}" \
#     \( -name "*.dylib" -o -name "*.so" -o -perm +111 \) \
#     -type f -print0 |
# while IFS= read -r -d '' bin; do
#     codesign --force --timestamp --options runtime \
#              --sign "${IDENTITY}" "${bin}"
# done

echo "⚠️  sign_python_framework.sh is a stub — wired up in M5."
echo "    Framework: ${FRAMEWORK}"
