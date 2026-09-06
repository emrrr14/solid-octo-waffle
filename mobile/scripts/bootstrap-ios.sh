#!/usr/bin/env bash
#
# Generate the native iOS project for this app.  macOS + Xcode only.
#
# The repo deliberately does NOT contain a checked-in `.xcodeproj`: a hand-written
# pbxproj drifts from whatever React Native version you actually install and fails
# in ways that are miserable to debug.  Instead we generate the official template
# for the pinned RN version and graft our Swift module and Info.plist keys onto it.
#
#   ./scripts/bootstrap-ios.sh
#   npm run mock         # terminal 2 - fake backend
#   npm start            # terminal 3 - metro
#   npm run ios          # terminal 4 - simulator
#
set -euo pipefail

APP_NAME="RoboAdvisor"
RN_VERSION="$(node -p "require('./package.json').dependencies['react-native']")"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ "$(uname)" != "Darwin" ]]; then
  echo "iOS builds require macOS with Xcode. Aborting." >&2
  exit 1
fi

if [[ -d "$ROOT/ios/$APP_NAME.xcodeproj" ]]; then
  echo "ios/$APP_NAME.xcodeproj already exists - nothing to do."
  echo "Delete it first if you want to regenerate from the template."
  exit 0
fi

echo "==> Generating the React Native $RN_VERSION iOS template"
npx --yes @react-native-community/cli@latest init "$APP_NAME" \
  --version "$RN_VERSION" --directory "$TMP/$APP_NAME" --install-pods false --skip-git-init true

echo "==> Grafting the template's ios/ into this project"
# Our Swift sources live in ios/RoboAdvisor/ already; keep them.
mkdir -p "$ROOT/ios"
cp -R "$TMP/$APP_NAME/ios/." "$ROOT/ios/"
cp "$ROOT/ios/$APP_NAME/SecureVault.swift" "$ROOT/ios/$APP_NAME/" 2>/dev/null || true

PLIST="$ROOT/ios/$APP_NAME/Info.plist"

echo "==> Adding Info.plist keys"
# Face ID prompt string. Without it iOS kills the app the first time
# LocalAuthentication is invoked - a crash, not a permission denial.
/usr/libexec/PlistBuddy -c "Add :NSFaceIDUsageDescription string 'Portföy işlemlerinizi onaylamak için Face ID kullanılır'" "$PLIST" 2>/dev/null || true

# App Transport Security: cleartext is allowed for localhost ONLY, so the mock
# backend works in the simulator while production stays https/wss.
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity dict" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsLocalNetworking bool true" "$PLIST" 2>/dev/null || true

echo "==> pod install"
( cd "$ROOT/ios" && bundle install >/dev/null 2>&1 || true; pod install )

cat <<'NOTE'

==> One manual step remains (Xcode cannot be scripted reliably here):

    1. open ios/RoboAdvisor.xcworkspace
    2. drag SecureVault.swift and SecureVault.m from ios/RoboAdvisor/ into the
       RoboAdvisor target in the Project Navigator ("Copy items if needed" OFF,
       target checkbox ON)
    3. Xcode will offer to create an Objective-C bridging header - accept it.
       That header is what lets the Swift class see RCTPromiseResolveBlock.
    4. Signing & Capabilities -> select your team (Face ID needs a real device
       to test; the simulator can fake it with Features > Face ID > Enrolled)

Then: npm run mock, npm start, npm run ios
NOTE
