#!/usr/bin/env python3
"""
Unified audio playback backend for Smart Azan.

The actual "audio doesn't work" bug: PulseAudio/PipeWire's *default* sink is
not necessarily the speaker you want. On a freshly booted Pi with no
Bluetooth speaker connected yet, the default sink is a dummy `auto_null` -
every play call succeeds with no error and simply produces no sound. Every
prior version of this app called `paplay <file>` / `ffplay <file>` with no
device argument at all, so it always played to whatever the implicit
default was, never to the Bluetooth/ALSA device actually configured.

This module fixes that by resolving an explicit target device from config
and always passing it to the player, and decodes every file through ffmpeg
first so the same device-targeting works regardless of source format
(mp3/wav/ogg/...) and regardless of whether the system runs PipeWire,
classic PulseAudio, or plain ALSA only (Pi Zero with no sound server).
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request

PLAY_TIMEOUT = 1800  # seconds - generous cap (30 min); only meant to catch a
                      # genuinely hung process, not to bound real recordings
                      # (a full azan/Quran recitation can run several minutes)

SNAPCAST_SAMPLE_RATE = 48000  # must match the [stream] sampleformat in snapserver.conf

_HAS = {}


def _which(name):
    if name not in _HAS:
        _HAS[name] = shutil.which(name) is not None
    return _HAS[name]


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


# ---------------------------------------------------------------------
# Discovery - what outputs actually exist on this machine right now
# ---------------------------------------------------------------------

def list_pulse_sinks():
    """Every PulseAudio/PipeWire sink currently known to the sound server."""
    if not _which("pactl"):
        return []
    r = _run(["pactl", "list", "short", "sinks"])
    if not r or r.returncode != 0:
        return []
    sinks = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name = parts[1]
            if name == "auto_null":
                continue
            kind = "bluetooth" if name.startswith("bluez_output.") else (
                "hdmi" if "hdmi" in name.lower() else "other"
            )
            sinks.append({"backend": "pulse", "id": name, "kind": kind,
                           "label": _friendly_sink_label(name, kind)})
    return sinks


def _friendly_sink_label(name, kind):
    if kind == "bluetooth":
        mac = name.split(".")[1].replace("_", ":").upper() if "." in name else name
        return f"Bluetooth speaker ({mac})"
    if kind == "hdmi":
        return f"HDMI audio ({name})"
    return name


def default_pulse_sink():
    if not _which("pactl"):
        return None
    r = _run(["pactl", "info"])
    if not r or r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.lower().startswith("default sink:"):
            name = line.split(":", 1)[1].strip()
            return None if name == "auto_null" else name
    return None


def list_alsa_cards():
    """Raw ALSA hardware devices - works even with no sound server at all
    (minimal Pi Zero images running plain ALSA)."""
    if not _which("aplay"):
        return []
    r = _run(["aplay", "-l"])
    if not r or r.returncode != 0:
        return []
    cards = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("card "):
            continue
        try:
            head, _, rest = line.partition(",")
            card_num = head.split(":")[0].replace("card", "").strip()
            dev_num = rest.split(":")[0].replace("device", "").strip()
            desc = head.split("[", 1)[1].rstrip("]") if "[" in head else head
            device_str = f"hw:{card_num},{dev_num}"
            cards.append({"backend": "alsa", "id": device_str, "kind": "alsa",
                           "label": f"{desc} ({device_str})"})
        except Exception:
            continue
    return cards


def bluetooth_sink_for_mac(mac):
    if not mac:
        return None
    target = mac.lower().replace(":", "_")
    for s in list_pulse_sinks():
        if target in s["id"].lower():
            return s["id"]
    return None


def list_outputs(cfg=None):
    """Everything the settings UI can offer the user to pick from."""
    cfg = cfg or {}
    outputs = []
    seen = set()
    for s in list_pulse_sinks() + list_alsa_cards():
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        outputs.append(s)

    resolved_backend, resolved_target = resolve_target(cfg)
    return {
        "outputs": outputs,
        "default_pulse_sink": default_pulse_sink(),
        "bluetooth_connected_sink": bluetooth_sink_for_mac(cfg.get("bluetooth_mac")),
        "resolved_backend": resolved_backend,
        "resolved_target": resolved_target,
    }


# ---------------------------------------------------------------------
# Resolve which device to actually play to, given config
# ---------------------------------------------------------------------

def resolve_target(cfg):
    """Returns (backend, target) where backend is "pulse" or "alsa", or
    (None, None) if nothing usable was found."""
    cfg = cfg or {}
    mode = (cfg.get("audio_output_mode") or "auto").lower()

    if mode == "alsa":
        dev = cfg.get("alsa_device")
        if dev:
            return "alsa", dev
        cards = list_alsa_cards()
        return ("alsa", cards[0]["id"]) if cards else (None, None)

    if mode == "bluetooth":
        configured = cfg.get("bluetooth_sink")
        sink = None
        if configured and any(s["id"] == configured for s in list_pulse_sinks()):
            sink = configured
        if not sink:
            sink = bluetooth_sink_for_mac(cfg.get("bluetooth_mac"))
        if sink:
            return "pulse", sink
        # Configured for bluetooth but nothing is actually connected right
        # now - fall through to auto so the azan still plays somewhere
        # audible instead of silently going nowhere.

    if mode == "pulse":
        default = default_pulse_sink()
        if default:
            return "pulse", default

    # auto (and bluetooth/pulse fallthrough): prefer a connected bluetooth
    # sink, then a real (non-null) pulse default, then the first ALSA card.
    bt_sink = bluetooth_sink_for_mac(cfg.get("bluetooth_mac"))
    if bt_sink:
        return "pulse", bt_sink
    default = default_pulse_sink()
    if default:
        return "pulse", default
    cards = list_alsa_cards()
    if cards:
        return "alsa", cards[0]["id"]
    return None, None


# ---------------------------------------------------------------------
# Playback
#
# Every step below is deliberately a *direct* file argument to a player
# rather than a live pipe wherever possible (paplay/aplay natively read
# WAV/FLAC/OGG headers themselves - no decode step needed at all), and any
# unavoidable decode step (mp3 via mpg123) writes to a temp file rather than
# streaming through a pipe. This isn't just simpler: on hardware with a
# flaky storage device, a crashed decoder killing a live pipe silently ends
# playback early with no error, whereas a failed temp-file decode is caught
# and retried before anything is sent to the speaker.
# ---------------------------------------------------------------------

_NATIVE_EXTS = (".wav", ".wave", ".flac", ".ogg", ".oga", ".aiff", ".aif")
PLAY_ATTEMPTS = 3


def _play_native(path, backend, target, timeout):
    if backend == "pulse":
        cmd = ["paplay", "--device", target, path]
    else:
        cmd = ["aplay", "-q", "-D", target, path]
    subprocess.run(cmd, timeout=timeout, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _play_via_decode_to_tempfile(path, backend, target, timeout, gain_db=0):
    """For formats paplay/aplay can't read natively (mp3/m4a/aac), or
    whenever a gain boost is requested: decode fully to a temp WAV first
    (applying the gain filter if any), then play that like any native file.

    Gain is paired with a brick-wall limiter (alimiter) rather than being a
    bare volume multiply - several azan recordings already peak close to
    0 dBFS, so any positive gain without limiting would just clip and
    distort instead of actually sounding louder.
    """
    import tempfile
    lower = path.lower()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp_path = tf.name
    try:
        if lower.endswith((".mp3",)) and _which("mpg123") and not gain_db:
            subprocess.run(["mpg123", "-q", "-w", tmp_path, path],
                           timeout=timeout, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif _which("ffmpeg"):
            filter_args = []
            if gain_db:
                filter_args = ["-af", f"volume={gain_db}dB,alimiter=limit=0.95:level=false"]
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path] + filter_args + [tmp_path],
                           timeout=timeout, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            raise RuntimeError("no decoder available for this format (need mpg123 or ffmpeg)")

        if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError("decode produced no output")

        remaining = max(1, timeout - 5)
        _play_native(tmp_path, backend, target, remaining)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _play_once(path, backend, target, timeout, gain_db=0):
    lower = path.lower()
    if gain_db == 0 and lower.endswith(_NATIVE_EXTS) and (backend == "pulse" and _which("paplay")
                                                            or backend == "alsa" and _which("aplay")):
        _play_native(path, backend, target, timeout)
    else:
        _play_via_decode_to_tempfile(path, backend, target, timeout, gain_db)


# ---------------------------------------------------------------------
# Snapcast (multi-room) playback
#
# When enabled, azan/dua/Quran audio is written as raw PCM into the FIFO
# snapserver reads its stream from, instead of playing directly to a local
# device - snapserver then fans it out to every connected snapclient
# (including one running locally on this Pi, so this speaker keeps working
# even with just one "room"). This replaces direct playback rather than
# running alongside it, since doing both would play the same clip twice.
# ---------------------------------------------------------------------

def _snapcast_rpc(url, method, params=None, timeout=3):
    body = json.dumps({"id": 1, "jsonrpc": "2.0", "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _snapcast_set_all_client_volumes(jsonrpc_url, percent):
    try:
        status = _snapcast_rpc(jsonrpc_url, "Server.GetStatus")
        groups = status.get("result", {}).get("server", {}).get("groups", [])
        for g in groups:
            for client in g.get("clients", []):
                cid = client.get("id")
                if not cid:
                    continue
                _snapcast_rpc(jsonrpc_url, "Client.SetVolume",
                               {"id": cid, "volume": {"percent": percent, "muted": False}})
    except Exception as e:
        print(f"[Snapcast] volume control error: {e}")


def play_via_snapcast(path, cfg, timeout=PLAY_TIMEOUT):
    """Returns True/False; only meaningful when cfg['snapcast_enabled'] - the
    caller decides whether to use this or direct playback."""
    fifo_path = cfg.get("snapcast_fifo") or "/tmp/smartazan.fifo"
    if not os.path.exists(fifo_path):
        print(f"[Snapcast] fifo not found at {fifo_path} - is snapserver running?")
        return False
    if not _which("ffmpeg"):
        print("[Snapcast] ffmpeg not available, cannot decode for snapcast")
        return False

    try:
        gain_db = float(cfg.get("audio_gain_db", 0) or 0)
    except (TypeError, ValueError):
        gain_db = 0

    jsonrpc_url = cfg.get("snapcast_jsonrpc")
    duck_to = cfg.get("snapcast_duck_to", 35)
    restore_to = cfg.get("snapcast_restore_to", 80)
    if jsonrpc_url:
        _snapcast_set_all_client_volumes(jsonrpc_url, duck_to)

    decode = None
    try:
        filter_args = ["-af", f"volume={gain_db}dB,alimiter=limit=0.95:level=false"] if gain_db else []
        decode_cmd = ["ffmpeg", "-v", "error", "-i", path] + filter_args + ["-f", "s16le",
                      "-ar", str(SNAPCAST_SAMPLE_RATE), "-ac", "2", "-"]
        decode = subprocess.Popen(decode_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        # Plain open(path, "wb") implies O_CREAT|O_TRUNC, which the kernel
        # rejects on a FIFO owned by another user even with 0666 perms - a
        # bare O_WRONLY (no create/truncate) is all a FIFO write needs.
        fifo_fd = os.open(fifo_path, os.O_WRONLY)
        try:
            with os.fdopen(fifo_fd, "wb") as fifo:
                while True:
                    chunk = decode.stdout.read(65536)
                    if not chunk:
                        break
                    fifo.write(chunk)
        except BrokenPipeError:
            pass
        decode.wait(timeout=10)
        return True
    except Exception as e:
        print(f"[Snapcast] playback error: {e}")
        return False
    finally:
        if decode is not None and decode.poll() is None:
            decode.kill()
        if jsonrpc_url:
            time.sleep(1)  # let the last buffered chunk drain before restoring volume
            _snapcast_set_all_client_volumes(jsonrpc_url, restore_to)


def play(path, cfg, timeout=PLAY_TIMEOUT, attempts=PLAY_ATTEMPTS):
    """Play one audio file according to cfg's output settings. Never raises -
    logs and returns False on any failure so a caller loop (like the
    scheduler) keeps running.

    Retries a few times on failure: this codebase has hit systems where a
    failing disk sector makes an individual ffmpeg/mpg123/paplay invocation
    crash unpredictably (works one moment, Bus-errors the next) - a retry
    costs nothing and often succeeds where the previous attempt didn't.
    """
    if not os.path.isfile(path):
        print(f"[Audio] missing file: {path}")
        return False

    try:
        gain_db = float(cfg.get("audio_gain_db", 0) or 0)
    except (TypeError, ValueError):
        gain_db = 0

    if cfg.get("snapcast_enabled"):
        if play_via_snapcast(path, cfg, timeout):
            print(f"[Audio] played '{path}' via snapcast")
            return True
        print(f"[Audio] snapcast playback failed for '{path}', falling back to direct output")

    backend, target = resolve_target(cfg)
    if not backend or not target:
        print(f"[Audio] no output device available for '{path}'")
        return False

    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            _play_once(path, backend, target, timeout, gain_db)
            print(f"[Audio] played '{path}' via {backend} -> {target}"
                  + (f" (attempt {attempt})" if attempt > 1 else ""))
            return True
        except subprocess.TimeoutExpired:
            # A timeout means the file legitimately ran longer than `timeout`
            # (or something is genuinely hung) - retrying with the same
            # timeout would just time out again the same way, so don't burn
            # through more attempts for this failure mode.
            last_err = f"timed out after {timeout}s"
            print(f"[Audio] attempt {attempt}/{attempts} failed for '{path}': {last_err}")
            break
        except subprocess.CalledProcessError as e:
            last_err = f"exit code {e.returncode}"
        except Exception as e:
            last_err = str(e)
        print(f"[Audio] attempt {attempt}/{attempts} failed for '{path}': {last_err}")

    print(f"[Audio] gave up on '{path}' after {attempts} attempts: {last_err}")
    return False


def _make_tone_wav(path, frequency=880, duration=1.0, rate=44100):
    """Write a short sine-wave WAV using only the stdlib - no ffmpeg/external
    process involved, so the settings "Test sound" button still works even
    if ffmpeg itself is broken on this system."""
    import wave
    import math
    import struct

    n_samples = int(rate * duration)
    with wave.open(path, "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n_samples):
            val = int(32767 * 0.5 * math.sin(2 * math.pi * frequency * i / rate))
            frames += struct.pack("<hh", val, val)
        w.writeframes(bytes(frames))


def play_test_tone(backend=None, target=None, timeout=10, attempts=PLAY_ATTEMPTS):
    """Play a short synthesized beep - no dependency on any user-uploaded
    audio file, useful for the settings UI's "Test" button."""
    if backend is None or target is None:
        return False, "no audio output device available"

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp_path = tf.name
    try:
        _make_tone_wav(tmp_path)
        last_err = None
        for _ in range(attempts):
            try:
                _play_native(tmp_path, backend, target, timeout)
                return True, None
            except subprocess.TimeoutExpired:
                last_err = "timed out"
            except Exception as e:
                last_err = str(e)
        return False, last_err
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------

def set_volume(cfg, volume):
    backend, target = resolve_target(cfg)
    try:
        if backend == "pulse" and target and _which("pactl"):
            subprocess.run(["pactl", "set-sink-volume", target, f"{volume}%"], timeout=5, check=True)
            print(f"[Audio] volume set to {volume}% on {target}")
        elif backend == "alsa" and target and _which("amixer"):
            card = target.split(":")[1].split(",")[0] if ":" in target else "0"
            for control in ("Master", "PCM", "Speaker"):
                r = subprocess.run(["amixer", "-c", card, "sset", control, f"{volume}%"],
                                    capture_output=True, timeout=5)
                if r.returncode == 0:
                    print(f"[Audio] volume set to {volume}% on hw:{card} ({control})")
                    return
        elif _which("pactl"):
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"], timeout=5)
    except Exception as e:
        print(f"[Audio] set_volume error: {e}")
