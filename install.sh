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
  bluez network-manager

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

echo "==> Installing systemd service..."
sed "s/__USER__/$(whoami)/" smart-azan.service | sudo tee /etc/systemd/system/smart-azan.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable smart-azan.service
sudo systemctl restart smart-azan.service

echo
echo "==> Done. Smart Azan should now be running on boot."
echo "    Check status:   sudo systemctl status smart-azan"
echo "    View logs:       sudo journalctl -u smart-azan -f"
echo "    Web UI:          http://$(hostname -I | awk '{print $1}'):5050"
echo
echo "Next: open the web UI -> Settings -> Audio Output, pick/test your speaker"
echo "(Bluetooth, HDMI, USB DAC, or 3.5mm jack), then upload your azan audio files."
