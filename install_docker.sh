#!/usr/bin/env bash
# Sets up Smart Azan's actual production deployment (Docker) on any
# Debian/Ubuntu x86_64 or arm64 machine - this is what install.sh's
# systemd-service approach has been superseded by; that script is kept only
# for reference/older installs.
#
# Installs and configures everything the app needs on the HOST (Docker only
# runs the app itself - Bluetooth audio specifically has to go through the
# host's own PulseAudio/BlueZ, not something containerized):
#   - Docker + the Docker Compose plugin
#   - PulseAudio (+ its Bluetooth module) as an always-on user service
#   - BlueZ (bluetoothd)
#   - Snapcast (snapserver + snapclient) - the local audio pipe the app
#     writes to, piping into PulseAudio
#   - A self-signed HTTPS cert (needed for the browser Geolocation API)
#   - .env (Flask secret key + admin login password)
#
# Run this as the normal user the containers should run under (NOT root -
# the script uses sudo itself where needed):
#   cd smart_azan_final && ./install_docker.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Please run this as your normal user, not root (it will sudo when needed)." >&2
  exit 1
fi

if ! grep -qiE 'ubuntu|debian' /etc/os-release 2>/dev/null; then
  echo "Warning: this script is written for Debian/Ubuntu - continuing anyway," >&2
  echo "but package names/paths may not match on your distro." >&2
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# A clear, specific error here beats a confusing mid-script failure if this
# is accidentally run from the wrong directory, or before cloning at all.
if [ ! -f docker-compose.yml ] || [ ! -f app.py ]; then
  echo "error: docker-compose.yml/app.py not found in $PROJECT_DIR." >&2
  echo "Clone the repo first, then run this script from inside it:" >&2
  echo "  git clone https://github.com/sharjeelkiyani/smart-azan.git ~/apps/smart_azan_final" >&2
  echo "  cd ~/apps/smart_azan_final && ./install_docker.sh" >&2
  exit 1
fi

THIS_USER="$(whoami)"
THIS_UID="$(id -u)"

echo "==> Refreshing package index..."
sudo apt-get update

# ---------------------------------------------------------------------
# 1. Docker + Compose plugin
# ---------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Docker not found - installing from Docker's official apt repo..."
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  . /etc/os-release
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  sudo usermod -aG docker "$THIS_USER"
  echo "    Added $THIS_USER to the docker group - you'll need to log out/in"
  echo "    (or just continue - the script below uses sudo for docker where needed)."
else
  echo "==> Docker already installed ($(docker --version))."
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "==> Docker Compose plugin not found - installing..."
  sudo apt-get update
  sudo apt-get install -y docker-compose-plugin
else
  echo "==> Docker Compose plugin already installed ($(docker compose version --short 2>/dev/null))."
fi

# A fresh install of the docker group above doesn't apply to this already-
# running shell - fall back to sudo for docker commands in that case so the
# rest of this script still works without requiring a re-login first.
DOCKER="docker"
if ! docker ps >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi

# ---------------------------------------------------------------------
# 2. Host audio stack: PulseAudio, Bluetooth, Snapcast
# ---------------------------------------------------------------------
echo "==> Installing PulseAudio, Bluetooth, and Snapcast packages..."
sudo apt-get install -y \
  pulseaudio pulseaudio-module-bluetooth \
  bluez bluez-tools \
  snapserver snapclient \
  openssl

echo "==> Adding $THIS_USER to the audio group (needed to access sound hardware)..."
sudo usermod -aG audio "$THIS_USER"

echo "==> Enabling lingering for $THIS_USER (keeps PulseAudio running without a login session)..."
sudo loginctl enable-linger "$THIS_USER"

echo "==> Disabling PulseAudio's idle auto-exit (this is a dedicated audio server, not a desktop)..."
# Without this, the whole daemon exits after ~20s of silence and has to be
# lazily re-activated by the next client - every exit/restart cycle tears
# down and rebuilds its Bluetooth device registration, which looks exactly
# like the Bluetooth speaker randomly "connecting and disconnecting".
mkdir -p "$HOME/.config/pulse"
cat > "$HOME/.config/pulse/daemon.conf" <<'EOF'
exit-idle-time = -1
EOF

echo "==> Enabling Bluetooth..."
sudo systemctl enable --now bluetooth.service

echo "==> Setting up Snapcast (FIFO at /tmp/smartazan.fifo -> PulseAudio)..."
sudo tee /usr/local/bin/smartazan_mkfifo > /dev/null <<'EOF'
#!/bin/sh
[ -p /tmp/smartazan.fifo ] || mkfifo /tmp/smartazan.fifo
chown _snapserver:_snapserver /tmp/smartazan.fifo
chmod 666 /tmp/smartazan.fifo
EOF
sudo chmod +x /usr/local/bin/smartazan_mkfifo

sudo tee /etc/snapserver.conf > /dev/null <<'EOF'
[stream]
stream = pipe:///tmp/smartazan.fifo?name=Azan&sampleformat=48000:16:2&codec=flac
buffer = 1000
send_to_muted = false
EOF

sudo mkdir -p /etc/systemd/system/snapserver.service.d
sudo tee /etc/systemd/system/snapserver.service.d/override.conf > /dev/null <<'EOF'
[Service]
ExecStartPre=/usr/local/bin/smartazan_mkfifo
EOF

sudo mkdir -p /etc/systemd/system/snapclient.service.d
sudo tee /etc/systemd/system/snapclient.service.d/override.conf > /dev/null <<EOF
[Service]
User=$THIS_USER
Group=$THIS_USER
Environment="XDG_RUNTIME_DIR=/run/user/$THIS_UID"
Environment="PULSE_SERVER=/run/user/$THIS_UID/pulse/native"
ExecStart=
ExecStart=/usr/bin/snapclient --logsink=system --player pulse --host 127.0.0.1
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now snapserver snapclient

# ---------------------------------------------------------------------
# 3. AppArmor (Ubuntu only - Raspberry Pi OS doesn't have it)
# ---------------------------------------------------------------------
# Ubuntu's default Docker AppArmor profile blocks a container's D-Bus
# "Hello" call to the system bus outright, which breaks every bluetoothctl
# call from inside the container - already handled by
# `security_opt: [apparmor:unconfined]` in docker-compose.yml, nothing to
# do here, just noting why that line exists.

# ---------------------------------------------------------------------
# 4. App config: .env, audio folder, HTTPS cert
# ---------------------------------------------------------------------
if [ ! -f .env ]; then
  echo "==> Creating .env (Flask session key + admin login password)..."
  echo
  read -r -p "Set an admin password to protect the web UI (leave blank for no login, LAN-only use): " ADMIN_PW
  FLASK_KEY="$(openssl rand -hex 32)"
  {
    echo "FLASK_SECRET_KEY=$FLASK_KEY"
    echo "SMART_AZAN_ADMIN_PASSWORD=$ADMIN_PW"
  } > .env
  chmod 600 .env
else
  echo "==> .env already exists, leaving it as-is."
fi

if [ ! -f config.json ]; then
  echo "==> No config.json found - copying config.example.json as a starting point."
  cp config.example.json config.json
fi

mkdir -p audio
if [ -z "$(ls -A audio 2>/dev/null)" ]; then
  echo "==> audio/ is empty - upload your azan/dua recordings from the web UI (Settings) after starting."
fi

if [ ! -f cert.pem ] || [ ! -f cert.key ]; then
  echo "==> Generating a self-signed HTTPS certificate (needed for GPS location in the browser)..."
  ./gen_https_cert.sh
fi

# ---------------------------------------------------------------------
# 5. Build and start
# ---------------------------------------------------------------------
echo "==> Building and starting the Docker container..."
$DOCKER compose build
$DOCKER compose up -d

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "================================================================"
echo " Done."
echo "================================================================"
echo
echo "Local web UI:  https://$LAN_IP:5050"
echo "  (self-signed cert - your browser will warn once, click through it)"
echo
if [ -s .env ] && grep -q "SMART_AZAN_ADMIN_PASSWORD=.\+" .env; then
  echo "Login is enabled - use the password you just set."
else
  echo "Login is NOT enabled (no password set) - anyone on your LAN can open"
  echo "this without a password. Fine for local-only use; set"
  echo "SMART_AZAN_ADMIN_PASSWORD in .env (then 'docker compose up -d' again)"
  echo "before exposing this to the internet."
fi
echo
echo "To make this reachable from outside your LAN (a real domain instead of"
echo "the local IP above), you need, separately from this script:"
echo "  1. A domain/subdomain you control, pointed at your public IP"
echo "  2. A real HTTPS certificate for it (e.g. via acme.sh / Let's Encrypt)"
echo "  3. nginx (or similar) terminating that cert and reverse-proxying to"
echo "     this machine's https://127.0.0.1:5050 (proxy_ssl_verify off, since"
echo "     that backend cert is self-signed)"
echo "  4. A port-forward rule on your router to reach that nginx"
echo "This is site-specific (domain registrar, router) so it isn't automated"
echo "here - see the Public HTTPS section in README.md for the exact steps"
echo "used for smartazan.ssmarttec.com."
echo
if [ "$DOCKER" = "sudo docker" ]; then
  echo "NOTE: you were just added to the 'docker' group - log out and back in"
  echo "(or reboot) so you can run 'docker' commands without sudo from now on."
fi
echo "NOTE: if this is the first time $THIS_USER has been in the 'audio' group,"
echo "Bluetooth/PulseAudio may need you to log out and back in (or reboot) too"
echo "before audio output works - if 'docker compose up -d' above already"
echo "worked but there's no sound, that's almost certainly why."
echo
echo "Next: open the web UI -> Settings -> Audio Output, pick/test your"
echo "speaker (Bluetooth, HDMI, USB DAC, or 3.5mm jack), then upload your"
echo "azan audio files."
