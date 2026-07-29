#!/usr/bin/env python3
"""
Controllable media player for long-form listening: Quran recitations (local
files or a streaming URL) with pause/resume-from-position, and live radio
(e.g. Makkah/Madinah) which just plays/stops with no position tracking.

Unlike audio_player.py (fire-and-forget, used for scheduled azan/dua), this
needs real transport control - pause, resume, seek - which plain paplay/aplay
don't offer. mpv provides that over a small JSON IPC socket, plus native
device targeting and native URL streaming, so it's used here instead.
"""
import json
import os
import socket
import subprocess
import threading
import time
import uuid

import audio_player

STATE_FILE = "quran_state.json"
SOCK_PATH = "/tmp/smart_azan_mpv.sock"

_lock = threading.RLock()
_proc = None
_current_device = None

_saver_thread = None
_saver_stop = threading.Event()


# ---------------------------------------------------------------------
# State (track list + saved positions)
# ---------------------------------------------------------------------

def _load_state():
    if not os.path.exists(STATE_FILE):
        return {"tracks": [], "current_id": None}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"tracks": [], "current_id": None}


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def list_tracks():
    return _load_state()["tracks"]


def add_file_track(title, filename):
    state = _load_state()
    track = {"id": uuid.uuid4().hex[:12], "title": title or filename,
              "type": "file", "source": filename, "position": 0}
    state["tracks"].append(track)
    _save_state(state)
    return track


def add_url_track(title, url, live=False):
    state = _load_state()
    track = {"id": uuid.uuid4().hex[:12], "title": title or url,
              "type": "live" if live else "url", "source": url, "position": 0}
    state["tracks"].append(track)
    _save_state(state)
    return track


def remove_track(track_id):
    state = _load_state()
    state["tracks"] = [t for t in state["tracks"] if t["id"] != track_id]
    if state.get("current_id") == track_id:
        state["current_id"] = None
    _save_state(state)


def _find_track(state, track_id):
    return next((t for t in state["tracks"] if t["id"] == track_id), None)


# ---------------------------------------------------------------------
# mpv process + IPC
# ---------------------------------------------------------------------

def _mpv_device_string(backend, target):
    if backend == "pulse":
        return f"pulse/{target}"
    if backend == "alsa":
        return f"alsa/{target}"
    return "auto"


def _is_running():
    return _proc is not None and _proc.poll() is None


def _send(cmd, timeout=3):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCK_PATH)
        s.sendall((json.dumps({"command": cmd}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        line = buf.splitlines()[0] if buf else b"{}"
        return json.loads(line.decode())
    finally:
        s.close()


def _get_prop(name, default=None):
    try:
        r = _send(["get_property", name])
        return r.get("data", default)
    except Exception:
        return default


def ensure_player(cfg):
    """Start mpv idle if not already running with the right output device;
    restart it if the resolved device changed (e.g. Bluetooth reconnected to
    a different sink) since it was started."""
    global _proc, _current_device
    backend, target = audio_player.resolve_target(cfg)
    device = _mpv_device_string(backend, target)

    with _lock:
        if _is_running() and _current_device == device:
            return True
        if _is_running():
            _stop_process()
        try:
            if os.path.exists(SOCK_PATH):
                os.unlink(SOCK_PATH)
        except OSError:
            pass
        try:
            _proc = subprocess.Popen(
                ["mpv", "--no-video", "--idle=yes", "--really-quiet",
                 f"--input-ipc-server={SOCK_PATH}", f"--audio-device={device}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"[Quran] failed to start mpv: {e}")
            return False
        _current_device = device
        for _ in range(30):
            if os.path.exists(SOCK_PATH):
                return True
            time.sleep(0.1)
        return False


def _stop_process():
    global _proc
    if _proc is not None:
        try:
            _proc.terminate()
            _proc.wait(timeout=3)
        except Exception:
            try:
                _proc.kill()
            except Exception:
                pass
    _proc = None


def shutdown_player():
    _saver_stop.set()
    with _lock:
        _stop_process()


# ---------------------------------------------------------------------
# Playback control
# ---------------------------------------------------------------------

def _source_path(track, audio_folder="audio"):
    if track["type"] in ("url", "live"):
        return track["source"]
    return os.path.join(audio_folder, track["source"])


def play_track(cfg, track_id, audio_folder="audio"):
    with _lock:
        state = _load_state()
        track = _find_track(state, track_id)
        if not track:
            return False, "track not found"
        if not ensure_player(cfg):
            return False, "player failed to start (mpv unavailable)"

        src = _source_path(track, audio_folder)
        try:
            _send(["loadfile", src, "replace"])
            time.sleep(0.3)
            pos = (track.get("position") or 0) if track["type"] != "live" else 0
            if pos > 1:
                _send(["seek", pos, "absolute"])
            _send(["set_property", "pause", False])
        except Exception as e:
            return False, str(e)

        state["current_id"] = track_id
        _save_state(state)
        _start_position_saver(audio_folder)
        return True, None


def pause(audio_folder="audio"):
    _save_current_position(audio_folder)
    try:
        _send(["set_property", "pause", True])
        return True, None
    except Exception as e:
        return False, str(e)


def resume():
    try:
        _send(["set_property", "pause", False])
        return True, None
    except Exception as e:
        return False, str(e)


def stop(audio_folder="audio"):
    """Pause and remember position - listening resumes from the same spot
    next time, it does not restart from the beginning."""
    return pause(audio_folder)


def restart_from_beginning(audio_folder="audio"):
    with _lock:
        state = _load_state()
        cid = state.get("current_id")
        if not cid:
            return False, "nothing playing"
        try:
            _send(["seek", 0, "absolute"])
        except Exception as e:
            return False, str(e)
        for t in state["tracks"]:
            if t["id"] == cid:
                t["position"] = 0
        _save_state(state)
        return True, None


def seek(position_seconds, audio_folder="audio"):
    try:
        _send(["seek", position_seconds, "absolute"])
        _save_current_position(audio_folder)
        return True, None
    except Exception as e:
        return False, str(e)


def get_status():
    state = _load_state()
    if not _is_running():
        return {"playing": False, "paused": True, "position": 0, "duration": 0,
                 "current_id": state.get("current_id"), "player_ready": False}
    paused = _get_prop("pause", True)
    pos = _get_prop("playback-time", 0) or 0
    dur = _get_prop("duration", 0) or 0
    return {"playing": not paused, "paused": bool(paused), "position": pos,
             "duration": dur, "current_id": state.get("current_id"), "player_ready": True}


def _save_current_position(audio_folder="audio"):
    state = _load_state()
    cid = state.get("current_id")
    if not cid or not _is_running():
        return
    track = _find_track(state, cid)
    if not track or track["type"] == "live":
        return
    pos = _get_prop("playback-time")
    if pos is None:
        return
    track["position"] = pos
    _save_state(state)


def _position_saver_loop(audio_folder):
    while not _saver_stop.is_set():
        try:
            _save_current_position(audio_folder)
        except Exception:
            pass
        _saver_stop.wait(5)


def _start_position_saver(audio_folder="audio"):
    global _saver_thread
    if _saver_thread is None or not _saver_thread.is_alive():
        _saver_stop.clear()
        _saver_thread = threading.Thread(target=_position_saver_loop, args=(audio_folder,), daemon=True)
        _saver_thread.start()
