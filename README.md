# Smart Azan

A self-hosted Islamic prayer-time (azan) scheduler and player with a web UI,
built for Raspberry Pi (Zero through 5) or any Debian/Ubuntu x86_64/arm64
machine. It plays azan, iqama, and dua audio at scheduled times over
Bluetooth, HDMI, a USB DAC, or a 3.5mm jack, shows Qibla direction and a
prayer calendar, sends push notifications (with tap-to-play on a phone), and
can act as its own Wi-Fi hotspot for initial setup.

## Contents

- [Quick start (Docker)](#quick-start-docker)
- [Opening the web UI](#opening-the-web-ui)
- [Features](#features)
- [Audio backend](#audio-backend)
- [Hardware support](#hardware-support)
- [Public HTTPS / custom domain](#public-https--custom-domain)
- [Android TV app](#android-tv-app)
- [Alternate: systemd service, no Docker](#alternate-systemd-service-no-docker)
- [Configuration](#configuration)
- [Managing the service](#managing-the-service)

## Quick start (Docker)

This is the actual production deployment method - a systemd-service
alternative (no Docker) also exists, see
[Alternate: systemd service](#alternate-systemd-service-no-docker), but
Docker is what's tested and kept up to date.

1. On the machine that will run this (Raspberry Pi, Intel NUC, or any
   Debian/Ubuntu box):

   ```bash
   git clone https://github.com/sharjeelkiyani/smart-azan.git ~/apps/smart_azan_final
   cd ~/apps/smart_azan_final
   ./install_docker.sh
   ```

2. `install_docker.sh` installs and configures *everything* needed: Docker
   + Compose, PulseAudio (as an always-on service, not lazily started - see
   why in the script's comments), its Bluetooth module, BlueZ, Snapcast
   (the local audio pipe the app writes to), a `.env` with your chosen admin
   password, a self-signed HTTPS cert, and then builds and starts the
   container. It asks one question (the admin password) and otherwise runs
   unattended. At the end it prints the address to open, for example:

   ```
   Local web UI:  https://192.168.1.42:5050
   ```

3. Open that address in a browser on any phone/laptop on the same network -
   see [Opening the web UI](#opening-the-web-ui) below for the one-time
   security warning you'll see and how to get past it.

4. **If this is the first time this user has been added to the `docker` or
   `audio` groups**, log out and back in (or reboot) once - group membership
   doesn't apply to an already-running shell/session. The script tells you
   at the end if this applies to you.

That's the entire local setup - reachable on your own network at `https://
<this-machine's-ip>:5050`. No account, no cloud service, no app store. For a
real domain name reachable from outside your home network (like this
project's own `smartazan.ssmarttec.com`), see
[Public HTTPS / custom domain](#public-https--custom-domain) - that part is
inherently specific to your domain registrar and router, so it isn't
automated by the install script.

## Opening the web UI

**The address always starts with `https://`, not `http://`.** For example:

```
https://192.168.1.42:5050
```

(Replace `192.168.1.42` with your Pi's actual address - `install.sh` prints
it at the end, or run `hostname -I` on the Pi.)

This app uses HTTPS on purpose, even on your own home network: browsers only
allow the Geolocation API (used for Qibla direction and weather) on secure
(`https://`) origins, and refuse it entirely on a plain `http://<lan-ip>`
address.

### "Your connection is not private" warning

Because this is a self-signed certificate (there's no public certificate
authority for a private home IP address), your browser will show a warning
the **first time** you visit. This is expected and safe to proceed past - it
just means the browser doesn't recognize the certificate issuer, not that
anything is actually wrong.

- **Chrome/Edge/Android**: click **Advanced**, then **Proceed to
  `<ip>` (unsafe)**.
- **Safari/iOS**: tap **Show Details**, then **visit this website**, and
  confirm.
- **Firefox**: click **Advanced...**, then **Accept the Risk and Continue**.

You only need to do this once per browser/device - it's remembered after
that. If the Pi's IP address later changes (e.g. after a router reboot),
re-run `./gen_https_cert.sh` on the Pi and restart the service (see
[Managing the service](#managing-the-service)), then click through the
warning again on each device.

### Add it to your phone's home screen

The app is an installable PWA. After opening it once:

- **Android/Chrome**: menu (⋮) -> **Add to Home screen**.
- **iOS/Safari**: Share button -> **Add to Home Screen**.

It then opens full-screen like a normal app, without the browser bar.

## Features

- Web UI for uploading azan/iqama/dua audio per prayer, editing the daily
  prayer timetable, and managing settings
- Scheduler polls a CSV timetable and plays the right audio within a
  30-second window of each prayer/iqama time, plus optional recurring or
  day-specific duas and a Friday khutbah (uploaded file or a live radio
  relay, e.g. Makkah)
- Automatic prayer-timetable import/sync from a mosque's published timetable
  (currently: Aisha Masjid), including both Jumu'ah slots, checked daily
- Qibla Finder with live GPS tracking and a device-compass overlay
- Automatic light/dark theme based on today's Maghrib-to-Fajr window
- Bluetooth pairing/connect flow (`bluetoothctl`-based) for wireless speakers
- Wi-Fi management from Settings: connect to a network, or fall back to a
  `SmartAzanPi` hotspot when no known network is in range
- Optional Snapcast integration for multi-room audio, with automatic
  volume duck/restore around each azan/dua
- Robust audio backend (see below) that auto-detects and targets the right
  sound output instead of silently playing to nothing

## Audio backend

Every audio file is decoded once with `ffmpeg` and piped into an explicitly
targeted player, so device selection is guaranteed regardless of the source
format (mp3/wav/ogg/...) instead of silently playing to a disconnected or
dummy device:

- **Bluetooth** - piped into `paplay --device <bluez sink>`
- **PulseAudio/PipeWire default** - piped into `paplay --device <default sink>`
- **ALSA** (3.5mm jack, USB DAC, HDMI, or any system with no sound server at
  all, e.g. a minimal Pi Zero image) - piped into `aplay -D <hw:X,Y>`
- **Auto** (default) - prefers a connected Bluetooth speaker, falls back to
  a real Pulse/PipeWire sink, falls back to the first ALSA card

Settings -> Audio Output lets you pick a mode and a specific device from
what's actually detected on your hardware, has a "Test sound" button to
confirm audio is audible before relying on it for Fajr, and a digital
volume-boost slider (with built-in limiting) if 100% still isn't loud enough
on your speaker.

## Hardware support

Runs in production on both a Raspberry Pi and an Intel NUC (Ubuntu). Audio
output works the same way on either:

- **Bluetooth speaker** (including smart speakers like an Echo Show, which
  needs the `pulseaudio-module-bluetooth` package `install_docker.sh`
  installs) via BlueZ/PulseAudio.
- **3.5mm jack / HDMI / USB DAC** via the host's own ALSA/PulseAudio sound
  card.
- **Pi Zero / Zero W / Zero 2 W** - no analog jack on the original Zero;
  use a USB audio adapter or Bluetooth.

On a non-Raspberry-Pi-OS host (e.g. Ubuntu), `install_docker.sh` also adds a
`security_opt: [apparmor:unconfined]` requirement that's already baked into
`docker-compose.yml` - Ubuntu's default Docker AppArmor profile otherwise
blocks the container's D-Bus access entirely, breaking every Bluetooth
operation. Raspberry Pi OS doesn't enable AppArmor, so this is a no-op there.

## Public HTTPS / custom domain

To reach this from outside your home network under a real domain (instead
of `https://<lan-ip>:5050`), you need, separately from `install_docker.sh`:

1. **A domain or subdomain you control**, with an A/CNAME record pointing at
   your public IP (or a dynamic-DNS name if your IP isn't static).
2. **A real certificate for it.** If your DNS registrar has no API for
   `acme.sh`'s DNS-01 challenge (e.g. Squarespace), delegate just the
   `_acme-challenge` subdomain via CNAME to a provider that does have one
   (e.g. DuckDNS), then:
   ```bash
   export DuckDNS_Token='<your token>'
   acme.sh --issue --domain yourdomain.example.com \
     --challenge-alias your-duckdns-name.duckdns.org \
     --dns dns_duckdns --keylength ec-256
   ```
3. **nginx** (outside the container, on the host or another machine on your
   LAN) terminating that real cert and reverse-proxying to this app's own
   self-signed HTTPS port:
   ```nginx
   server {
       listen 8443 ssl;  # see note below on why not just 443
       server_name yourdomain.example.com;
       ssl_certificate     /path/to/fullchain.pem;
       ssl_certificate_key /path/to/privkey.pem;
       location / {
           proxy_pass https://<this-machine-ip>:5050;
           proxy_ssl_verify off;  # backend cert is self-signed, that's fine
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto https;
       }
   }
   ```
4. **A port-forward rule on your router** pointing some public port at that
   nginx instance. Port 443 is the obvious choice, but check first - if
   something else already forwards 443 to a different device, pick a free
   port instead (e.g. 8443) and include it in the URL.

Once this is set up, also set `SMART_AZAN_ADMIN_PASSWORD` in `.env` (see
[Configuration](#configuration)) - don't expose this to the public internet
without a login.

## Android TV app

A free, native Android/Fire TV app (`android-tv-app/`) is available as an
alternative to opening the site in a browser - it auto-detects a new azan
and plays it even if the app isn't already open, with a screensaver mode.
GitHub Actions builds it automatically on every push to `android-tv-app/**`
and publishes it to a rolling release (see `android-tv-app/README.md` for
manual `adb install` instructions).

If the app's own login is enabled (`SMART_AZAN_ADMIN_PASSWORD` set), the
running server also exposes a password-gated direct download link, so a
Fire TV/Android TV box can fetch the APK itself (via Silk Browser, or a
sideloading tool like Downloader) without needing a computer in between:

```
https://<your-domain-or-ip>:5050/download-tv-app?password=<your-admin-password>
```

The app is dedicated to one server address, hardcoded in
`MainActivity.kt`'s `DEFAULT_SERVER_URL` - update that (and let CI rebuild)
if you fork this for a different install, or override the address per
device via a long-press of Back on the remote.

## Alternate: systemd service, no Docker

An older, non-Docker install path also exists - a plain Python virtualenv
run via systemd instead of a container. It's not the actively-used
deployment (see [Quick start](#quick-start-docker) above for that), kept
only for reference or genuinely resource-constrained devices:

```bash
git clone https://github.com/sharjeelkiyani/smart-azan.git ~/smart_azan_final
cd ~/smart_azan_final
./install.sh
```

`install.sh` installs system packages (`ffmpeg`, `alsa-utils`,
`pulseaudio-utils`, `bluez`, `network-manager`), creates a Python
virtualenv, generates a self-signed cert, and installs
`smart-azan.service` (a systemd unit using `%h`/`%U` specifiers, so it
works for whichever user runs it as long as the project lives at
`~/smart_azan_final`). It runs under **gunicorn** (`gunicorn_conf.py`), not
Flask's dev server, always as a single worker (multiple would each start
their own copy of the background scheduler/Bluetooth/Wi-Fi threads, racing
to play the same azan multiple times).

Manual install without even `install.sh`:

```bash
sudo apt-get install -y python3-venv ffmpeg alsa-utils pulseaudio-utils bluez network-manager
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp config.example.json config.json
./gen_https_cert.sh
sed "s/__USER__/$(whoami)/" smart-azan.service | sudo tee /etc/systemd/system/smart-azan.service
sudo systemctl daemon-reload
sudo systemctl enable --now smart-azan
```

## Configuration

- `.env` (gitignored - see `.env.example`) - `FLASK_SECRET_KEY` (random,
  signs session cookies) and `SMART_AZAN_ADMIN_PASSWORD` (blank disables
  login entirely, fine for LAN-only use - `install_docker.sh` sets both up
  interactively)
- `config.json` (gitignored - real Wi-Fi passwords and your Bluetooth MAC
  live here) - start from `config.example.json`
- `cert.pem` / `cert.key` (gitignored, host-specific) - generated by
  `gen_https_cert.sh`; re-run it if this machine's LAN IP changes
- `timetable.csv` (gitignored, user-specific) - one row per day:

  ```
  Date,Fajr,Dhuhr,Asr,Maghrib,Isha,Iqama_Fajr,Iqama_Dhuhr,Iqama_Asr,Iqama_Maghrib,Iqama_Isha
  01/01/2026,06:28,12:12,14:19,16:09,17:31,07:15,13:00,14:45,16:11,19:30
  ```

  Upload it from Prayer Times, enable automatic mosque-timetable sync from
  Azan Settings, or drop a file at `timetable.csv` in the project root.
- `audio/` (gitignored - bring your own recordings) - upload azan/iqama/dua
  files per prayer from the web UI; nothing is bundled with the repo since
  recitations are generally not freely redistributable.

## Managing the service

**Docker (the actual deployment - see [Quick start](#quick-start-docker)):**

```bash
docker compose ps
docker compose logs -f smart-azan
docker compose restart smart-azan   # after a code change - no rebuild needed
docker compose up -d --build        # after a Dockerfile/requirements.txt change
```

**systemd (the [alternate, non-Docker](#alternate-systemd-service-no-docker) install):**

```bash
sudo systemctl status smart-azan
sudo systemctl restart smart-azan
sudo journalctl -u smart-azan -f
```

Web UI: `https://<this-machine's-ip>:5050` (port configurable in Settings;
see [Opening the web UI](#opening-the-web-ui) above for the HTTPS security
warning you'll see the first time).
