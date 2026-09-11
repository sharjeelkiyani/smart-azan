#!/usr/bin/env bash
# Sets up Smart Azan on any Debian-based Linux (Raspberry Pi OS - Zero
# through 5 - or a generic Debian/Ubuntu box) and installs it as a systemd
# service that starts on boot.
#
# Run this as the user the service should run under (NOT root/sudo directly -
# the script uses sudo itself where needed):
#   cd smart_azan_final && ./install.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Please run this as your normal user, not root (it will sudo when needed)." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ "$PROJECT_DIR" != "$HOME/smart_azan_final" ]; then
  echo "Warning: this project should live at \$HOME/smart_azan_final for the"
  echo "systemd service (which uses %h) to find it. Currently at: $PROJECT_DIR"
fi

echo "==> Installing system packages (ffmpeg, ALSA, PulseAudio/PipeWire client tools, Bluetooth, NetworkManager)..."
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  ffmpeg mpg123 mpv alsa-utils pulseaudio-utils \
  bluez network-manager rfkill

echo "==> Setting up a passwordless helper to power on Bluetooth from the web UI..."
# Fresh Pi/Debian images commonly ship with the Bluetooth radio rfkill
# soft-blocked. Lifting that block needs root, and the web app runs as this
# user - not root - so give it a narrow, single-purpose sudo rule (same
# pattern as the existing nmcli Wi-Fi-connect helper) instead of broad
# passwordless sudo.
sudo tee /usr/local/bin/smart-azan-bt-power > /dev/null <<'EOF'
#!/bin/sh
rfkill unblock bluetooth
bluetoothctl power on
EOF
sudo chmod 755 /usr/local/bin/smart-azan-bt-power
echo "$(whoami) ALL=(ALL) NOPASSWD: /usr/local/bin/smart-azan-bt-power" | \
  sudo tee /etc/sudoers.d/99-smart-azan-bt-power > /dev/null
sudo chmod 440 /etc/sudoers.d/99-smart-azan-bt-power

echo "==> Creating Python virtual environment..."
if [ ! -d venv ]; then
  python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [ ! -f config.json ]; then
  echo "==> No config.json found - copying config.example.json as a starting point."
  cp config.example.json config.json
  echo "    Edit config.json (Wi-Fi networks, hotspot password, prayer calc method) before going further."
fi

mkdir -p audio
if [ -z "$(ls -A audio 2>/dev/null)" ]; then
  echo "==> audio/ is empty - upload your azan/dua recordings from the web UI (Settings) after starting the service."
fi

if [ ! -f cert.pem ] || [ ! -f cert.key ]; then
  echo "==> Generating a self-signed HTTPS certificate (needed for GPS location to work in the browser)..."
  ./gen_https_cert.sh
fi

echo "==> Installing systemd service..."
# Substituted directly rather than relying on systemd's own %h/%U specifiers -
# those are mishandled on some newer systemd versions (confirmed: systemd 257
# on Debian 13 fails WorkingDirectory=%h/... with a spurious "Permission
# denied" on CHDIR, even though the same absolute path works fine).
sed -e "s/__USER__/$(whoami)/" -e "s#__HOME__#$HOME#g" -e "s/__UID__/$(id -u)/" \
  smart-azan.service | sudo tee /etc/systemd/system/smart-azan.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable smart-azan.service
sudo systemctl restart smart-azan.service

echo
echo "==> Done. Smart Azan should now be running on boot."
echo "    Check status:   sudo systemctl status smart-azan"
echo "    View logs:       sudo journalctl -u smart-azan -f"
echo "    Web UI:          https://$(hostname -I | awk '{print $1}'):5050"
echo "                     (self-signed cert - your browser will warn once, click through it;"
echo "                     needed for GPS location to work)"
echo
echo "Next: open the web UI -> Settings -> Audio Output, pick/test your speaker"
echo "(Bluetooth, HDMI, USB DAC, or 3.5mm jack), then upload your azan audio files."
