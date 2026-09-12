#!/usr/bin/env bash
# Installs (or updates) the Smart Azan TV app on a Fire TV over the network,
# and grants the one permission Fire OS can't expose a settings screen for.
#
# Works from any machine with `adb` installed (the Smart Azan Pi, a laptop,
# anywhere on the same network as the Fire TV) - always pulls the latest
# built APK straight from GitHub, so there's nothing to keep a local copy of
# or re-download by hand.
#
# Usage:
#   ./install-on-firetv.sh <fire-tv-ip> [package-name]
#
# First time on a given Fire TV, you must first enable, on the TV itself:
#   Settings -> My Fire TV -> Developer Options -> ADB debugging
# (if "Developer Options" isn't visible: My Fire TV -> About -> click the
# device name 7 times to unlock it)
# Then, when this script connects, the TV will show an "Allow USB
# debugging?" prompt - accept it (tick "Always allow from this computer" so
# this is only needed once).
set -euo pipefail

APK_URL="https://github.com/sharjeelkiyani/smart-azan/releases/download/tv-app-latest/app-debug.apk"
PACKAGE="${2:-com.smartazan.tvdisplay}"

if [ -z "${1:-}" ]; then
  echo "Usage: $0 <fire-tv-ip> [package-name]" >&2
  exit 1
fi
IP="$1"
TARGET="$IP:5555"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb is not installed on this machine. Install it first:" >&2
  echo "  Debian/Ubuntu/Raspberry Pi OS: sudo apt install android-tools-adb" >&2
  echo "  macOS (Homebrew):              brew install android-platform-tools" >&2
  echo "  Windows:                       https://developer.android.com/tools/releases/platform-tools" >&2
  exit 1
fi

echo "==> Connecting to $TARGET..."
adb connect "$TARGET"

STATE="$(adb -s "$TARGET" get-state 2>&1 || true)"
if [ "$STATE" != "device" ]; then
  echo
  echo "Not yet authorized - check the Fire TV's screen for an \"Allow USB"
  echo "debugging?\" prompt, accept it (tick \"Always allow from this"
  echo "computer\"), then re-run this script."
  exit 1
fi

TMP_APK="$(mktemp --suffix=.apk 2>/dev/null || mktemp -t smartazan).apk"
trap 'rm -f "$TMP_APK"' EXIT

echo "==> Downloading latest build..."
curl -fL -o "$TMP_APK" "$APK_URL"

echo "==> Installing on $IP..."
adb -s "$TARGET" install -r "$TMP_APK"

echo "==> Granting \"draw over other apps\" (needed for azan to take over the"
echo "    screen automatically - Fire OS has no settings screen for this,"
echo "    adb is the only way to grant it)..."
adb -s "$TARGET" shell appops set "$PACKAGE" SYSTEM_ALERT_WINDOW allow

echo "==> Launching..."
adb -s "$TARGET" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1 >/dev/null

echo
echo "==> Done. If this is the first install on this TV, it'll ask for the"
echo "    Smart Azan server's TV display address (Settings -> Integrations"
echo "    on the web UI shows it) - enter it once with the remote."
