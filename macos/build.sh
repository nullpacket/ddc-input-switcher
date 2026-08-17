#!/usr/bin/env bash
# Builds MonitorInputSwitcher.app from main.swift.
# Requires the Xcode Command Line Tools: xcode-select --install
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="MonitorInputSwitcher"
BUNDLE="${APP_NAME}.app"
CONTENTS="${BUNDLE}/Contents"

if ! command -v swiftc >/dev/null 2>&1; then
    echo "error: swiftc not found. Install the Xcode Command Line Tools:" >&2
    echo "       xcode-select --install" >&2
    exit 1
fi

if ! command -v m1ddc >/dev/null 2>&1; then
    echo "warning: m1ddc not on PATH. Install it with: brew install m1ddc" >&2
fi

echo "==> Cleaning"
rm -rf "$BUNDLE"
mkdir -p "${CONTENTS}/MacOS"

echo "==> Compiling"
swiftc -O -framework AppKit -o "${CONTENTS}/MacOS/${APP_NAME}" main.swift

echo "==> Writing Info.plist"
cat > "${CONTENTS}/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Monitor Input Switcher</string>
    <key>CFBundleDisplayName</key>
    <string>Monitor Input Switcher</string>
    <key>CFBundleIdentifier</key>
    <string>net.nullpacket.monitorinputswitcher</string>
    <key>CFBundleExecutable</key>
    <string>MonitorInputSwitcher</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

echo "==> Ad-hoc signing"
codesign --force --sign - "$BUNDLE"

echo
echo "Built ${BUNDLE}"
echo "Run it with:  open ${BUNDLE}"
echo "Install with: cp -r ${BUNDLE} /Applications/"
