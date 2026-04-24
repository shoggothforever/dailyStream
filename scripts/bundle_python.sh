#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# bundle_python.sh — pack a standalone CPython + dailystream-core for the Swift
#                    shell to embed under Contents/Frameworks/Python.framework.
#
# What it does
#   1. Download a matching python-build-standalone (PBS) release.
#   2. Extract it into apps/DailyStreamMac/Frameworks/Python.framework.
#   3. pip-install the dailystream project into the bundled Python.
#   4. Smoke-test by running `dailystream-core --version` via the bundled
#      interpreter, and also pumping a couple of JSON-RPC messages through
#      stdin/stdout.
#
# What it does NOT do (yet — deferred to M5 "release" step)
#   * Code signing (`codesign`) every embedded .dylib.
#   * Stapling a notary ticket.
#   * Stripping down Python to reduce size (standard layout kept for now).
#
# Usage
#   scripts/bundle_python.sh                 # arm64, Python 3.11
#   scripts/bundle_python.sh --python 3.12   # pick another version
#   scripts/bundle_python.sh --arch x86_64   # force Intel build
#   scripts/bundle_python.sh --force         # nuke existing Framework dir
# -----------------------------------------------------------------------------

set -euo pipefail

# --- defaults -----------------------------------------------------------------
PY_VERSION="3.11.11"
PBS_RELEASE="20250106"     # tag on indygreg/python-build-standalone
ARCH_DEFAULT="$(uname -m)" # arm64 on Apple Silicon, x86_64 on Intel
ARCH="${ARCH_DEFAULT}"
FORCE=0

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${REPO_ROOT}/apps/DailyStreamMac/Frameworks"
FRAMEWORK_DIR="${OUT_DIR}/Python.framework"
CACHE_DIR="${REPO_ROOT}/.bundle-cache"

# --- args ---------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python) PY_VERSION="$2"; shift 2 ;;
        --release) PBS_RELEASE="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# //;s/^#//'
            exit 0
            ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

case "${ARCH}" in
    arm64)  PBS_TRIPLE="aarch64-apple-darwin" ;;
    x86_64) PBS_TRIPLE="x86_64-apple-darwin" ;;
    *) echo "Unsupported arch: ${ARCH}" >&2; exit 1 ;;
esac

PBS_FLAVOR="install_only"
PBS_FILE="cpython-${PY_VERSION}+${PBS_RELEASE}-${PBS_TRIPLE}-${PBS_FLAVOR}.tar.gz"
PBS_URL="https://github.com/indygreg/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILE}"

echo "════════════════════════════════════════════════════════════"
echo "  DailyStream — bundle Python"
echo "    python = ${PY_VERSION}"
echo "    release= ${PBS_RELEASE}"
echo "    arch   = ${ARCH}  (${PBS_TRIPLE})"
echo "    out    = ${FRAMEWORK_DIR}"
echo "════════════════════════════════════════════════════════════"

mkdir -p "${CACHE_DIR}" "${OUT_DIR}"

# --- download -----------------------------------------------------------------
CACHED_TAR="${CACHE_DIR}/${PBS_FILE}"
if [[ ! -f "${CACHED_TAR}" ]]; then
    echo "▶ downloading ${PBS_FILE}"
    curl --fail --location --progress-bar \
         --output "${CACHED_TAR}" "${PBS_URL}"
else
    echo "▶ using cached tarball: ${CACHED_TAR}"
fi

# --- extract ------------------------------------------------------------------
if [[ -d "${FRAMEWORK_DIR}" ]]; then
    if [[ ${FORCE} -eq 1 ]]; then
        echo "▶ removing existing framework (--force)"
        rm -rf "${FRAMEWORK_DIR}"
    else
        echo "▶ framework already exists; use --force to rebuild"
        exit 0
    fi
fi

TMP_EXTRACT="${CACHE_DIR}/extract-$$"
mkdir -p "${TMP_EXTRACT}"
echo "▶ extracting"
tar -xzf "${CACHED_TAR}" -C "${TMP_EXTRACT}"

# PBS install_only layout: <root>/python/{bin,lib,include,share}
PYROOT="${TMP_EXTRACT}/python"
if [[ ! -d "${PYROOT}" ]]; then
    echo "Unexpected tarball layout: ${PYROOT} not found" >&2
    exit 1
fi

# Lay out as a .framework so Swift can reference it via @rpath /
# @executable_path/../Frameworks/Python.framework/Versions/3.x/bin/python3
VERSION_MAJOR_MINOR="$(basename "${PYROOT}/lib/python"*)"
VERSION_MAJOR_MINOR="${VERSION_MAJOR_MINOR#python}"

FRAMEWORK_VER_DIR="${FRAMEWORK_DIR}/Versions/${VERSION_MAJOR_MINOR}"
mkdir -p "${FRAMEWORK_VER_DIR}"
cp -R "${PYROOT}/bin" "${FRAMEWORK_VER_DIR}/bin"
cp -R "${PYROOT}/lib" "${FRAMEWORK_VER_DIR}/lib"
cp -R "${PYROOT}/include" "${FRAMEWORK_VER_DIR}/include" || true
cp -R "${PYROOT}/share"   "${FRAMEWORK_VER_DIR}/share"   || true

# Convenient top-level symlink (Apple convention)
ln -sfn "Versions/${VERSION_MAJOR_MINOR}" "${FRAMEWORK_DIR}/Current" || true

rm -rf "${TMP_EXTRACT}"

PY_BIN="${FRAMEWORK_VER_DIR}/bin/python3"
if [[ ! -x "${PY_BIN}" ]]; then
    echo "python3 not executable at ${PY_BIN}" >&2
    exit 1
fi

# --- install dailystream into bundle -----------------------------------------
echo "▶ installing dailystream (+ai extras) into bundle"
"${PY_BIN}" -m pip install --upgrade --quiet pip
# Editable install from repo root so later changes auto-reflect.
# Use --no-deps + explicit deps install to keep layer tidy.
"${PY_BIN}" -m pip install --quiet "${REPO_ROOT}[ai]"

# --- smoke tests -------------------------------------------------------------
CORE_BIN="${FRAMEWORK_VER_DIR}/bin/dailystream-core"
if [[ ! -x "${CORE_BIN}" ]]; then
    echo "dailystream-core entry point not found at ${CORE_BIN}" >&2
    exit 1
fi

echo "▶ smoke test 1: import works"
"${PY_BIN}" -c "import dailystream, dailystream.rpc_server; print('dailystream', dailystream.__version__)"

echo "▶ smoke test 2: RPC ping/shutdown round-trip"
RESP="$(printf '%s\n%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"app.ping"}' \
    '{"jsonrpc":"2.0","id":2,"method":"app.shutdown"}' \
    | "${CORE_BIN}" 2>/dev/null)"
echo "${RESP}"
if ! echo "${RESP}" | grep -q '"result": "pong"'; then
    echo "❌ ping did not return pong" >&2
    exit 1
fi

cat <<EOF

✓ bundle ready
  Framework : ${FRAMEWORK_DIR}
  Python    : ${PY_BIN}
  RPC entry : ${CORE_BIN}

Next steps
  • Reference the framework from the Swift Xcode project:
      apps/DailyStreamMac → Build Phases → Embed Frameworks
  • Spawn dailystream-core from Swift via:
      Contents/Frameworks/Python.framework/Versions/${VERSION_MAJOR_MINOR}/bin/dailystream-core
  • Signing + notarization will be handled by scripts/build_release.sh
    in M5 (省心模式: skipped for now).
EOF
