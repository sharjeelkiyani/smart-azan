FROM python:3.11-slim-bookworm

# nmcli (wifi.py), bluetoothctl (bluetooth.py), adb (fire_tv.py) - the host binaries
# aren't visible inside the container, only the sockets they talk over (D-Bus, network),
# so each CLI tool needs its own client installed here too. Pinned to -bookworm
# specifically (matching the Pi's own Debian release, "python:3.11-slim" alone can drift
# to a newer Debian release over time) - nmcli talks to the host's NetworkManager over
# D-Bus using a property schema that changed between releases; a newer nmcli here than
# NetworkManager on the host silently breaks operations like creating the WiFi hotspot
# ("connection.autoconnect-ports: unknown property").
# audio_player.py/quran_player.py shell out to all of these the same way -
# pactl/paplay (pulseaudio-utils), aplay/amixer (alsa-utils), mpg123 and
# ffmpeg (mp3/m4a/aac -> wav conversion for paplay/aplay, which only read
# native formats), and mpv (Quran playback's IPC-controlled player). None
# of them were in the original image, so every playback path silently had
# nothing to actually run - audio_player.py's own fallback chain reported
# "no output device available" instead of a missing-binary error because
# _which() treats a tool that isn't installed the same as one that's
# installed but genuinely has no sink to play to.
RUN apt-get update && apt-get install -y --no-install-recommends \
    network-manager \
    bluez \
    android-tools-adb \
    sudo \
    pulseaudio-utils \
    alsa-utils \
    mpg123 \
    ffmpeg \
    mpv \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "-c", "gunicorn_conf.py", "app:app"]
