# Smart Azan

A self-hosted Islamic prayer-time (azan) scheduler and player with a web UI,
built for Raspberry Pi (Zero through 5) and generic Linux. It plays azan,
iqama, and dua audio at scheduled times over Bluetooth, HDMI, a USB DAC, or a
3.5mm jack, and can act as its own Wi-Fi hotspot for initial setup.

## Features

- Web UI for uploading azan/iqama/dua audio per prayer, editing the daily
  prayer timetable, and managing settings
- Scheduler polls a CSV timetable and plays the right audio within a
  30-second window of each prayer/iqama time, plus optional recurring or
  day-specific duas and a Friday dua
- Bluetooth pairing/connect flow (`bluetoothctl`-based) for wireless speakers
- Wi-Fi management: connect to a network, or fall back to a
  `SmartAzanPi` hotspot when no known network is in range
- Robust audio backend (see below) that auto-detects and targets the right
  sound output instead of silently playing to nothing

## Audio backend

Previously, playback called `paplay <file>` with no target device - on a
system where PulseAudio/PipeWire's default sink is a dummy placeholder (which
it is until a real device is connected), this "succeeds" while producing no
sound at all. Every audio file is now decoded once with `ffmpeg` and piped
into an explicitly targeted player, so device selection is guaranteed
regardless of the source format (mp3/wav/ogg/...):

- **Bluetooth** - piped into `paplay --device <bluez sink>`
- **PulseAudio/PipeWire default** - piped into `paplay --device <default sink>`
- **ALSA** (3.5mm jack, USB DAC, HDMI, or any system with no sound server at
  all, e.g. a minimal Pi Zero image) - piped into `aplay -D <hw:X,Y>`
- **Auto** (default) - prefers a connected Bluetooth speaker, falls back to
  a real Pulse/PipeWire sink, falls back to the first ALSA card

Settings -> Audio Output lets you pick a mode and a specific device from
what's actually detected on your hardware, and has a "Test sound" button
that plays a short tone to confirm audio is actually audible before you rely
on it for Fajr.

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

## Installation

```bash
git clone <this-repo-url> ~/smart_azan_final
cd ~/smart_azan_final
./install.sh
```

`install.sh` installs required system packages (`ffmpeg`, `alsa-utils`,
`pulseaudio-utils`, `bluez`, `network-manager`), creates a Python venv,
copies `config.example.json` to `config.json` if you don't have one yet, and
installs/enables the `smart-azan` systemd service so it starts on boot.

The systemd unit (`smart-azan.service`) uses systemd's `%h`/`%U` specifiers
to resolve the project path and runtime directory from whichever user it
runs as, so it works unmodified as long as the project lives at
`~/smart_azan_final` for that user. Only `User=` needs to be filled in
(`install.sh` does this automatically).

### Manual install (no install.sh)

```bash
sudo apt-get install -y python3-venv ffmpeg alsa-utils pulseaudio-utils bluez network-manager
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp config.example.json config.json
sed "s/__USER__/$(whoami)/" smart-azan.service | sudo tee /etc/systemd/system/smart-azan.service
sudo systemctl daemon-reload
sudo systemctl enable --now smart-azan
```

## Configuration

- `config.json` (gitignored - real Wi-Fi passwords and your Bluetooth MAC
  live here) - start from `config.example.json`
- `timetable.csv` (gitignored, user-specific) - one row per day:

  ```
  Date,Fajr,Dhuhr,Asr,Maghrib,Isha,Iqama_Fajr,Iqama_Dhuhr,Iqama_Asr,Iqama_Maghrib,Iqama_Isha
  01/01/2026,06:28,12:12,14:19,16:09,17:31,07:15,13:00,14:45,16:11,19:30
  ```

  Upload it from Settings, or drop a file at `timetable.csv` in the project
  root.
- `audio/` (gitignored - bring your own recordings) - upload azan/iqama/dua
  files per prayer from the web UI; nothing is bundled with the repo since
  recitations are generally not freely redistributable.

## Managing the service

```bash
sudo systemctl status smart-azan
sudo systemctl restart smart-azan
sudo journalctl -u smart-azan -f
```

Web UI: `http://<pi-ip>:5050` (port configurable in `config.json`).
