# Smart Azan

A self-hosted Islamic prayer-time (azan) scheduler and player with a web UI,
built for Raspberry Pi (Zero through 5) and generic Linux. It plays azan,
iqama, and dua audio at scheduled times over Bluetooth, HDMI, a USB DAC, or a
3.5mm jack, shows Qibla direction and a prayer calendar, and can act as its
own Wi-Fi hotspot for initial setup.

## Contents

- [Quick start](#quick-start)
- [Opening the web UI](#opening-the-web-ui)
- [Features](#features)
- [Audio backend](#audio-backend)
- [Hardware support](#hardware-support)
- [Manual install](#manual-install-no-installsh)
- [Configuration](#configuration)
- [Managing the service](#managing-the-service)

## Quick start

1. On the Raspberry Pi (or Linux machine) that will run this:

   ```bash
   git clone https://github.com/sharjeelkiyani/smart-azan.git ~/smart_azan_final
   cd ~/smart_azan_final
   ./install.sh
   ```

2. `install.sh` installs everything needed (system packages, Python
   dependencies, an HTTPS certificate, and a systemd service so it starts
   automatically on every boot). At the end it prints the address to open,
   for example:

   ```
   Web UI:          https://192.168.1.42:5050
   ```

3. Open that address in a browser on any phone/laptop on the same network -
   see [Opening the web UI](#opening-the-web-ui) below for the one-time
   security warning you'll see and how to get past it.

That's the entire setup. No account, no cloud service, no app store - it's a
web page served directly from the device sitting next to your speaker.

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

Tested on a Raspberry Pi 5 running PipeWire. Designed to also work on:

- **Pi Zero / Zero W / Zero 2 W** - no analog jack on the original Zero;
  use a USB audio adapter or Bluetooth. `install.sh` installs ALSA/Bluetooth
  tooling either way.
- **Pi 3/4** - onboard 3.5mm jack, HDMI, USB, or Bluetooth all work via the
  ALSA/Pulse backends above.
- **Pi 5** - no analog jack on most builds; use HDMI, USB, or Bluetooth.
- **Generic Debian/Ubuntu Linux** - `install.sh` targets `apt`; adapt the
  package list for other distros.

## What `install.sh` does

```bash
git clone https://github.com/sharjeelkiyani/smart-azan.git ~/smart_azan_final
cd ~/smart_azan_final
./install.sh
```

- Installs required system packages (`ffmpeg`, `alsa-utils`,
  `pulseaudio-utils`, `bluez`, `network-manager`)
- Creates a Python virtual environment and installs dependencies
- Copies `config.example.json` to `config.json` if you don't have one yet
- Generates a self-signed HTTPS certificate for your Pi's current LAN IP(s)
  (`gen_https_cert.sh`) if one doesn't already exist
- Installs and enables the `smart-azan` systemd service so it starts on boot
- Prints the web UI address to open when it finishes

The systemd unit (`smart-azan.service`) uses systemd's `%h`/`%U` specifiers
to resolve the project path and runtime directory from whichever user it
runs as, so it works unmodified as long as the project lives at
`~/smart_azan_final` for that user. Only `User=` needs to be filled in
(`install.sh` does this automatically).

The service runs under **gunicorn** (`gunicorn_conf.py`), not Flask's
built-in dev server - the dev server leaks connections under sustained load
(several browser tabs polling status endpoints, a router forwarding traffic
to it, etc.) until it can no longer accept new ones, which is exactly what
its own "do not use in production" warning is about. `gunicorn_conf.py`
reads the port and HTTPS cert from the same `config.json`/`cert.pem`/
`cert.key` files the app itself uses, always runs a single worker (multiple
would each start their own copy of the background scheduler/Bluetooth/Wi-Fi
threads, racing to play the same azan multiple times), and bounds the TLS
handshake so a client that drops mid-connection (e.g. a phone losing signal)
can't tie up a worker thread indefinitely.

## Manual install (no install.sh)

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

- `config.json` (gitignored - real Wi-Fi passwords and your Bluetooth MAC
  live here) - start from `config.example.json`
- `cert.pem` / `cert.key` (gitignored, host-specific) - generated by
  `gen_https_cert.sh`; re-run it if the Pi's LAN IP changes
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

```bash
sudo systemctl status smart-azan
sudo systemctl restart smart-azan
sudo journalctl -u smart-azan -f
```

Web UI: `https://<pi-ip>:5050` (port configurable in Settings; see
[Opening the web UI](#opening-the-web-ui) above for the HTTPS security
warning you'll see the first time).
