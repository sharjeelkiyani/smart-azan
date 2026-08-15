#!/usr/bin/env python3
import os
import json
import csv
import subprocess
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, render_template

# local modules
import wifi
import bluetooth
import routes_azan
import routes_quran
import routes_dashboard
import audio_player
import history_log
import islamic_utils
import quran_player

# ----------------- constants -----------------
AUDIO_FOLDER = "audio"
CONFIG_FILE = "config.json"
TIMETABLE_FILE = "timetable.csv"

# ----------------- flask -----------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change_this_secret_in_production")

# shared lock + config
config_lock = threading.Lock()


# ----------------- config helpers -----------------
def load_config():
    if not os.path.exists(CONFIG_FILE):
        cfg = {
            "lat": 0,
            "lon": 0,
            "method": "ISNA",
            "use_auto": False,
            "azan_audio": "default_azan.mp3",
            "azan_audio_per_prayer": {
                "Fajr": "default_azan.mp3",
                "Dhuhr": "default_azan.mp3",
                "Asr": "default_azan.mp3",
                "Maghrib": "default_azan.mp3",
                "Isha": "default_azan.mp3",
            },
            "duas": [],
            "friday_dua": {
                "file": "", "time": "", "khutbah_time": "", "khutbah_file": "",
                "khutbah_mode": "file", "khutbah_relay_track_id": "", "khutbah_relay_minutes": 45,
            },
            "iqama_audio": "iqama.mp3",
            "output_device": "auto",
            "audio_output_mode": "auto",
            "alsa_device": "",
            "bluetooth_mac": None,
            "bluetooth_sink": None,
            "speaker_name": "Main Speaker",
            "volume": 70,
            "audio_gain_db": 0,
            "hotspot_ssid": "SmartAzanPi",
            "hotspot_password": "changeme123",
            "hotspot_enabled": False,
            "auto_hotspot_enabled": False,
            "wifi_autoconnect": True,
            "preferred_wifi_ssid": "",
            "wifi_networks": {},
            "after_azan_dua": "",
            "port": 5050,
            "notifications_enabled": True,
            "reminder_minutes_before_azan": 10,
            "mosque_import_enabled": False,
            "mosque_import_source": "aisha_masjid",
        }
        save_config(cfg)
        return cfg

    with open(CONFIG_FILE) as f:
        cfg = json.load(f)

    # backfill
    cfg.setdefault("azan_audio", "default_azan.mp3")
    cfg.setdefault("azan_audio_per_prayer", {})
    cfg.setdefault("duas", [])
    cfg.setdefault("friday_dua", {"file": "", "time": ""})
    cfg["friday_dua"].setdefault("khutbah_time", "")
    cfg["friday_dua"].setdefault("khutbah_file", "")
    cfg["friday_dua"].setdefault("khutbah_mode", "file")
    cfg["friday_dua"].setdefault("khutbah_relay_track_id", "")
    cfg["friday_dua"].setdefault("khutbah_relay_minutes", 45)
    cfg.setdefault("output_device", "auto")
    cfg.setdefault("audio_output_mode", cfg.get("output_device", "auto"))
    cfg.setdefault("alsa_device", "")
    cfg.setdefault("speaker_name", "Main Speaker")
    cfg.setdefault("volume", 70)
    cfg.setdefault("audio_gain_db", 0)
    cfg.setdefault("port", 5050)
    cfg.setdefault("wifi_networks", {})
    cfg.setdefault("wifi_autoconnect", True)
    cfg.setdefault("after_azan_dua", "")
    cfg.setdefault("notifications_enabled", True)
    cfg.setdefault("reminder_minutes_before_azan", 10)
    cfg.setdefault("mosque_import_enabled", False)
    cfg.setdefault("mosque_import_source", "aisha_masjid")

    save_config(cfg)
    return cfg


def save_config(cfg):
    # preserve wifi stuff if caller forgot
    existing = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    for k in ("wifi_networks", "preferred_wifi_ssid", "wifi_autoconnect"):
        if k in existing and k not in cfg:
            cfg[k] = existing[k]

    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# make sure audio dir exists
os.makedirs(AUDIO_FOLDER, exist_ok=True)

# load config once at startup
with config_lock:
    cfg = load_config()

# ----------------- init submodules (robust) -----------------
# wifi: some versions have 4 args, some 6 – try 6, fall back to 4
try:
    wifi.init(app, config_lock, load_config, save_config, CONFIG_FILE, TIMETABLE_FILE)
except TypeError:
    wifi.init(app, config_lock, load_config, save_config)

# bluetooth should be simple
bluetooth.init(app, config_lock, load_config, save_config)

# routes_azan: same pattern – if your file takes fewer args, we fall back
try:
    routes_azan.init(
        app,
        config_lock,
        load_config,
        save_config,
        audio_folder=AUDIO_FOLDER,
        timetable_file=TIMETABLE_FILE,
        static_folder="static",
    )
except TypeError:
    routes_azan.init(app, config_lock, load_config, save_config)

routes_quran.init(app, config_lock, load_config, save_config, audio_folder=AUDIO_FOLDER)
routes_dashboard.init(app, config_lock, load_config, save_config, timetable_file=TIMETABLE_FILE)

# start wifi background daemons (autoconnect + hotspot)
# (this calls monitor_network_and_hotspot() inside wifi.py)
if hasattr(wifi, "start_background_threads"):
    wifi.start_background_threads()


# ----------------- bluetooth auto-reconnect -----------------
# Bluetooth speakers (including Echo devices used as A2DP sinks) do not
# reliably auto-reconnect after this Pi reboots or the speaker briefly loses
# power/range - without this, azan silently "plays" to whatever ALSA/HDMI
# fallback auto mode picks instead, and nothing is heard until someone
# manually reconnects from Settings.
#
# Checking via bluetoothctl's interactive shell (spawned fresh each time) is
# slow enough that it can itself race with bluetoothd ("Waiting to connect to
# bluetoothd...") and misreport an already-connected device as disconnected -
# which then fires a real `connect` at a live A2DP link and can knock it
# offline for real. Checking whether PipeWire/Pulse currently has a sink for
# this MAC is a single fast query with no such race, so it's used as the
# primary signal; bluetoothctl is only invoked when that says nothing is
# connected.
def _bluetooth_autoconnect_loop():
    misses = 0
    last_default_set = None
    while True:
        with config_lock:
            c = load_config()
        mac = c.get("bluetooth_mac")
        mode = (c.get("audio_output_mode") or "auto").lower()
        if mac and mode in ("bluetooth", "auto"):
            if audio_player.bluetooth_sink_for_mac(mac):
                misses = 0
            else:
                misses += 1
                # require two consecutive misses before acting, in case a
                # sink is just briefly absent right as playback starts/stops
                if misses >= 2:
                    print(f"[Bluetooth] {mac} not connected, attempting reconnect…")
                    bluetooth.ensure_bluetooth_ready()
                    bluetooth.run_bluetoothctl_cmd(["connect", mac])
                    misses = 0

        # Keep PipeWire/Pulse's *default* sink pointed at whatever
        # audio_player resolves to. Our own playback always targets a
        # device explicitly, so this doesn't affect it - it exists for
        # secondary consumers that don't support explicit device selection
        # (snapclient's pulse backend only ever plays to "default").
        backend, target = audio_player.resolve_target(c)
        if backend == "pulse" and target and target != last_default_set:
            try:
                subprocess.run(["pactl", "set-default-sink", target], timeout=5, check=True)
                last_default_set = target
                print(f"[Audio] default sink set to {target}")
            except Exception as e:
                print(f"[Audio] set-default-sink error: {e}")

        time.sleep(30)


threading.Thread(target=_bluetooth_autoconnect_loop, daemon=True).start()


# ----------------- mosque timetable auto-sync -----------------
# Opt-in (mosque_import_enabled) - re-fetches the configured mosque's
# published timetable once a day and merges any changed times into
# timetable.csv. The site only ever shows "this month", so a daily check is
# what actually picks up the new month automatically as it rolls over, and
# picks up any time corrections the mosque publishes mid-month too.
def _mosque_import_loop():
    last_run_date = None
    while True:
        with config_lock:
            c = load_config()
        today_str = datetime.now().strftime("%Y-%m-%d")
        if c.get("mosque_import_enabled") and last_run_date != today_str:
            source = c.get("mosque_import_source", "")
            try:
                import mosque_import
                if source == "aisha_masjid":
                    rows = mosque_import.fetch_aisha_masjid_timetable()
                    imported, total = mosque_import.merge_into_timetable(rows, TIMETABLE_FILE)
                    print(f"[MosqueImport] auto-synced {imported} day(s) from {source} ({total} total in timetable)")
                last_run_date = today_str
            except Exception as e:
                print(f"[MosqueImport] auto-sync failed: {e}")
        time.sleep(3600)


threading.Thread(target=_mosque_import_loop, daemon=True).start()


# ----------------- audio for scheduler -----------------
def play_audio(filename, event_type="manual", label=None):
    path = os.path.join(AUDIO_FOLDER, filename)
    with config_lock:
        cfg_now = load_config()
    ok = audio_player.play(path, cfg_now)
    history_log.log_event(event_type, label or event_type, filename, ok)
    return ok


_khutbah_relay_stop_at = None


# ----------------- scheduler thread -----------------
def scheduler():
    global _khutbah_relay_stop_at
    print("[Scheduler] Running…")
    played_events = set()
    last_min = -1
    while True:
        now = datetime.now()

        if _khutbah_relay_stop_at and now >= _khutbah_relay_stop_at:
            print("[Scheduler] Stopping Friday khutbah live relay (duration elapsed)")
            quran_player.stop(audio_folder=AUDIO_FOLDER)
            _khutbah_relay_stop_at = None
        if now.minute != last_min:
            played_events.clear()
            last_min = now.minute

        current_minute_str = now.strftime("%Y/%m/%d %H:%M")
        today_name = now.strftime("%A").lower()

        with config_lock:
            current_cfg = load_config()

        # --- 1) CSV-based azan + iqama ---
        if os.path.exists(TIMETABLE_FILE):
            try:
                with open(TIMETABLE_FILE) as csvfile:
                    for raw_row in csv.DictReader(csvfile):
                        if (raw_row.get("Date") or "").strip() == now.strftime("%d/%m/%Y"):
                            # normalize
                            row = {(k or "").strip(): (v or "").strip() for k, v in raw_row.items()}

                            for prayer in ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]:
                                # azan
                                pt = row.get(prayer)
                                if pt:
                                    try:
                                        pt_dt = datetime.strptime(
                                            f"{now.year}/{now.month:02d}/{now.day:02d} {pt}",
                                            "%Y/%m/%d %H:%M",
                                        )
                                        eid = f"azan_{prayer}_{current_minute_str}"
                                        if abs((pt_dt - now).total_seconds()) < 30 and eid not in played_events:
                                            azan_file = current_cfg["azan_audio_per_prayer"].get(
                                                prayer, current_cfg["azan_audio"]
                                            )
                                            print(f"[Scheduler] Playing {prayer} azan ({azan_file})")
                                            play_audio(azan_file, "azan", f"{prayer} azan")
                                            played_events.add(eid)

                                            # after-azan dua
                                            after_dua = (current_cfg.get("after_azan_dua") or "").strip()
                                            if after_dua:
                                                print(f"[Scheduler] Playing after-azan dua ({after_dua})")
                                                play_audio(after_dua, "dua", f"Dua after {prayer} azan")
                                    except Exception as e:
                                        print(f"[Scheduler] azan time parse error {prayer} ({pt}):", e)

                                # iqama
                                iq_key_1 = f"Iqama_{prayer}"
                                iq_key_2 = f"Iqama {prayer}"
                                iq_time = row.get(iq_key_1) or row.get(iq_key_2)
                                if iq_time:
                                    try:
                                        iq_dt = datetime.strptime(
                                            f"{now.year}/{now.month:02d}/{now.day:02d} {iq_time}",
                                            "%Y/%m/%d %H:%M",
                                        )
                                        iq_eid = f"iqama_{prayer}_{current_minute_str}"
                                        if abs((iq_dt - now).total_seconds()) < 30 and iq_eid not in played_events:
                                            iq_file = current_cfg.get("iqama_audio", "iqama.mp3")
                                            print(f"[Scheduler] Playing {prayer} iqama ({iq_file})")
                                            play_audio(iq_file, "iqama", f"{prayer} iqama")
                                            played_events.add(iq_eid)
                                    except Exception as e:
                                        print(f"[Scheduler] iqama time parse error {prayer} ({iq_time}):", e)

                            break
            except Exception as e:
                print("[Scheduler] CSV read error:", e)

        # --- 2) DUAS with day support ---
        for i, dua in enumerate(current_cfg.get("duas", [])):
            file_ = dua.get("file")
            t = (dua.get("time") or "").strip()
            day = (dua.get("day") or "daily").strip().lower()
            if not (file_ and t):
                continue
            if day not in ("daily", today_name):
                continue
            if t == now.strftime("%H:%M"):
                eid = f"dua_{i}_{current_minute_str}"
                if eid not in played_events:
                    print(f"[Scheduler] Playing dua {file_} for {day} at {t}")
                    play_audio(file_, "dua", f"Dua ({day} {t})")
                    played_events.add(eid)

        # --- 3) Friday special: dua + khutbah ---
        friday_dua = current_cfg.get("friday_dua") or {}
        if today_name == "friday":
            ffile = (friday_dua.get("file") or "").strip()
            ftime = (friday_dua.get("time") or "").strip()
            if ffile and ftime == now.strftime("%H:%M"):
                eid = f"friday_{current_minute_str}"
                if eid not in played_events:
                    print(f"[Scheduler] Playing Friday dua {ffile}")
                    play_audio(ffile, "friday_dua", "Friday dua")
                    played_events.add(eid)

            ktime = (friday_dua.get("khutbah_time") or "").strip()
            kmode = (friday_dua.get("khutbah_mode") or "file").strip()

            if kmode == "live_relay":
                track_id = (friday_dua.get("khutbah_relay_track_id") or "").strip()
                if track_id and ktime == now.strftime("%H:%M"):
                    keid = f"khutbah_relay_{current_minute_str}"
                    if keid not in played_events:
                        try:
                            relay_minutes = int(friday_dua.get("khutbah_relay_minutes") or 45)
                        except (TypeError, ValueError):
                            relay_minutes = 45
                        print(f"[Scheduler] Starting Friday khutbah live relay (track {track_id}, {relay_minutes} min)")
                        ok, err = quran_player.play_track(current_cfg, track_id, audio_folder=AUDIO_FOLDER)
                        history_log.log_event("khutbah", "Friday khutbah (live relay)", track_id, ok)
                        if ok:
                            _khutbah_relay_stop_at = now + timedelta(minutes=relay_minutes)
                        else:
                            print(f"[Scheduler] khutbah live relay failed to start: {err}")
                        played_events.add(keid)
            else:
                kfile = (friday_dua.get("khutbah_file") or "").strip()
                if kfile and ktime == now.strftime("%H:%M"):
                    keid = f"khutbah_{current_minute_str}"
                    if keid not in played_events:
                        print(f"[Scheduler] Playing Friday khutbah {kfile}")
                        play_audio(kfile, "khutbah", "Friday khutbah")
                        played_events.add(keid)

        time.sleep(10)


def _read_timetable_row(date_str):
    if not os.path.exists(TIMETABLE_FILE):
        return None
    try:
        with open(TIMETABLE_FILE) as csvfile:
            for row in csv.DictReader(csvfile):
                if (row.get("Date") or "").strip() == date_str:
                    return row
    except Exception as e:
        print("[Index] timetable read error:", e)
    return None


def _night_window(now):
    """(maghrib_dt, next_fajr_dt) bracketing 'night' for the auto day/night
    theme - Maghrib (sunset) through the next Fajr (dawn), the same boundary
    Islamically used for the night. Returns (None, None) if today's/
    tomorrow's timetable rows aren't available."""
    today_row = _read_timetable_row(now.strftime("%d/%m/%Y"))
    maghrib_dt = None
    if today_row:
        t = (today_row.get("Maghrib") or "").strip()
        if t:
            try:
                maghrib_dt = datetime.strptime(f"{now.strftime('%Y/%m/%d')} {t}", "%Y/%m/%d %H:%M")
            except ValueError:
                pass

    fajr_day = now if now.strftime("%H:%M") < "12:00" else now + timedelta(days=1)
    fajr_row = _read_timetable_row(fajr_day.strftime("%d/%m/%Y"))
    fajr_dt = None
    if fajr_row:
        t = (fajr_row.get("Fajr") or "").strip()
        if t:
            try:
                fajr_dt = datetime.strptime(f"{fajr_day.strftime('%Y/%m/%d')} {t}", "%Y/%m/%d %H:%M")
            except ValueError:
                pass

    return maghrib_dt, fajr_dt


@app.context_processor
def _inject_globals():
    now = datetime.now()
    maghrib_dt, fajr_dt = _night_window(now)
    return {
        "hijri_today": islamic_utils.hijri_date_string(now),
        "night_maghrib_iso": maghrib_dt.isoformat() if maghrib_dt else None,
        "night_fajr_iso": fajr_dt.isoformat() if fajr_dt else None,
    }


PRAYER_NAMES = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]


def _compute_next_prayer(now):
    """Returns (name, datetime) of the next upcoming prayer, looking at
    today's remaining prayers first and tomorrow's Fajr as a fallback."""
    today_row = _read_timetable_row(now.strftime("%d/%m/%Y"))
    today_times = []
    if today_row:
        for p in PRAYER_NAMES:
            t = (today_row.get(p) or "").strip()
            if not t:
                continue
            try:
                dt = datetime.strptime(f"{now.strftime('%Y/%m/%d')} {t}", "%Y/%m/%d %H:%M")
            except ValueError:
                continue
            today_times.append({"name": p, "time": t, "dt": dt})

    upcoming = [t for t in today_times if t["dt"] > now]
    past = [t for t in today_times if t["dt"] <= now]
    prev_dt = max((t["dt"] for t in past), default=now - timedelta(hours=6))
    if upcoming:
        nxt = min(upcoming, key=lambda t: t["dt"])
        return nxt["name"], nxt["dt"], today_times, today_row, prev_dt

    tomorrow = now + timedelta(days=1)
    tomorrow_row = _read_timetable_row(tomorrow.strftime("%d/%m/%Y"))
    if tomorrow_row:
        t = (tomorrow_row.get("Fajr") or "").strip()
        if t:
            try:
                dt = datetime.strptime(f"{tomorrow.strftime('%Y/%m/%d')} {t}", "%Y/%m/%d %H:%M")
                return "Fajr", dt, today_times, today_row, prev_dt
            except ValueError:
                pass
    return None, None, today_times, today_row, prev_dt


def _ntp_status():
    try:
        out = subprocess.run(["timedatectl", "show"], capture_output=True, text=True, timeout=3).stdout
        info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        return info.get("NTPSynchronized") == "yes"
    except Exception:
        return None


# ----------------- index / overview dashboard -----------------
@app.route("/")
def index():
    now = datetime.now()
    today_str = now.strftime("%d/%m/%Y")

    with config_lock:
        current_cfg = load_config()

    next_prayer, next_prayer_dt, today_times, today_row, prev_prayer_dt = _compute_next_prayer(now)
    weather = islamic_utils.get_weather(current_cfg.get("lat"), current_cfg.get("lon"))
    audio_devices = audio_player.list_outputs(current_cfg)
    recent_history = history_log.get_recent(6)

    return render_template(
        "overview.html",
        cfg=current_cfg,
        today_times=today_times,
        today_row=today_row,
        next_prayer=next_prayer,
        next_prayer_iso=next_prayer_dt.isoformat() if next_prayer_dt else None,
        prev_prayer_iso=prev_prayer_dt.isoformat() if prev_prayer_dt else None,
        weather=weather,
        audio_devices=audio_devices,
        ntp_synced=_ntp_status(),
        recent_history=recent_history,
        current_date=today_str,
        now=now,
    )


# Scheduler needs to run whether this module is launched directly (python
# app.py) or imported by a WSGI server (gunicorn), so it starts unconditionally
# here rather than inside `if __name__ == "__main__"`.
threading.Thread(target=scheduler, daemon=True).start()


# ----------------- main -----------------
# Only used for `python app.py` directly. The systemd service instead runs
# this under gunicorn (see gunicorn_conf.py) - Werkzeug's dev server leaks
# connections under sustained load (repeated polling from open browser tabs,
# multiple devices) until it can no longer accept new ones, which is exactly
# what "not production" means in its own startup warning. gunicorn is a real
# WSGI server and doesn't have that problem.
if __name__ == "__main__":
    with config_lock:
        port = int(load_config().get("port", 5050))

    cert_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cert.pem")
    key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cert.key")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        print(f"[Server] HTTPS enabled (cert: {cert_path})")
        app.run(host="0.0.0.0", port=port, ssl_context=(cert_path, key_path), threaded=True)
    else:
        app.run(host="0.0.0.0", port=port, threaded=True)
