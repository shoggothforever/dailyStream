#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# build_release.sh — produce a signed + notarized DailyStream.app (M5).
#
# 省心模式 (M0-M4)
#   This script is a stub.  M0 only needs bundle_python.sh.  The
#   signed / notarized release flow is assembled in M5 once the Swift
#   shell exists and you have provisioned a Developer ID identity.
#
# Planned flow (M5)
#   1. xcodebuild -project apps/DailyStreamMac/DailyStreamMac.xcodeproj \
#                 -scheme DailyStream -configuration Release archive
#   2. scripts/bundle_python.sh --force       # fresh framework
#   3. scripts/sign_python_framework.sh "$DEV_ID"
#   4. codesign --deep --sign "$DEV_ID" --entitlements ... DailyStream.app
#   5. xcrun notarytool submit --wait DailyStream.zip
#   6. xcrun stapler staple DailyStream.app
#   7. produce a .dmg (create-dmg or hdiutil)
# -----------------------------------------------------------------------------

cat >&2 <<'EOF'
⚠️  scripts/build_release.sh is a stub — implemented in M5.
    To test the RPC server end-to-end now, run:
      scripts/bundle_python.sh
      scripts/dev_run.sh
EOF
exit 64
