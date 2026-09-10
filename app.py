#!/usr/bin/env python3
import os
import json
import csv
import subprocess
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, render_template, request, send_from_directory

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
import fire_tv

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
            "tv_display_enabled": False,
            "fully_kiosk_url": "",
            "fully_kiosk_password": "",
            "tv_http_port": 5051,
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
    cfg.setdefault("tv_display_enabled", False)
    cfg.setdefault("fully_kiosk_url", "")
    cfg.setdefault("fully_kiosk_password", "")
    cfg.setdefault("tv_http_port", 5051)

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
#
# play_audio_fn is a lambda (not the play_audio function itself) because
# play_audio() is defined further down in this file - the lambda body isn't
# evaluated until routes_azan actually calls it (on a real HTTP request,
# long after this module has finished loading), so the forward reference is
# safe. Without this, routes_azan.py's manual-test routes (like "Test Azan")
# fell back to a local, separate play-audio implementation that plays the
# audio correctly but never notifies the Fire TV display - it played on the
# Bluetooth speaker but never showed up on the TV.
try:
    routes_azan.init(
        app,
        config_lock,
        load_config,
        save_config,
        audio_folder=AUDIO_FOLDER,
        timetable_file=TIMETABLE_FILE,
        static_folder="static",
        play_audio_fn=lambda filename, event_type="manual", label=None: play_audio(filename, event_type, label),
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
        # (snapclient's pulse backend only ever plays to "default", which is
        # how azan is actually reaching the speaker when Snapcast is on -
        # see audio_player.play_via_snapcast).
        #
        # Compares against the *live* current default (not "whatever we last
        # commanded") on every pass, so this is self-healing if something
        # else - PipeWire/WirePlumber's own auto-switching, a bluetooth
        # reconnect blip, another app opening an audio stream - changes the
        # real default sink without us knowing. Tracking only our own last
        # command missed exactly that case: our computed target hadn't
        # changed, so the stale bookkeeping said "nothing to do" even though
        # the actual default had silently drifted elsewhere, leaving
        # Snapcast (and so azan) routed away from the Bluetooth speaker.
        backend, target = audio_player.resolve_target(c)
        if backend == "pulse" and target and audio_player.default_pulse_sink() != target:
            try:
                subprocess.run(["pactl", "set-default-sink", target], timeout=5, check=True)
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
# Polled by /tv_status so any TV display page - Fully Kiosk, the Android/
# Fire TV app, or a plain browser tab left open - picks up a new azan/dua
# on its own, without needing a remote "load this now" command at all.
_tv_now_playing = {"filename": None, "play_id": 0}
_tv_now_playing_lock = threading.Lock()


def play_audio(filename, event_type="manual", label=None):
    path = os.path.join(AUDIO_FOLDER, filename)
    with config_lock:
        cfg_now = load_config()
    fire_tv.notify_display(cfg_now, audio_filename=filename)
    with _tv_now_playing_lock:
        _tv_now_playing["filename"] = filename
        _tv_now_playing["play_id"] += 1
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

    # A configured Bluetooth speaker that isn't currently connected means
    # azan is silently falling back to whatever else resolve_target() finds
    # (HDMI/ALSA) - often inaudible if nothing else is plugged in. Surface
    # this on the dashboard instead of only in the server log, since the
    # background auto-reconnect loop can't fix a speaker that's been fully
    # un-paired (not just out of range) - that needs a human to re-pair it.
    bt_mac = current_cfg.get("bluetooth_mac")
    bt_mode = (current_cfg.get("audio_output_mode") or "auto").lower()
    bluetooth_disconnected = bool(
        bt_mac and bt_mode in ("bluetooth", "auto")
        and not audio_devices.get("bluetooth_connected_sink")
    )

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
        bluetooth_disconnected=bluetooth_disconnected,
        ntp_synced=_ntp_status(),
        recent_history=recent_history,
        current_date=today_str,
        now=now,
    )


# ----------------- Fire TV / Fully Kiosk display -----------------
@app.route("/tv-display")
def tv_display():
    """Large-screen page for a Fire TV running Fully Kiosk Browser - see
    fire_tv.py. Not linked from the app's own nav; it's meant to be loaded
    remotely by Fully Kiosk's `loadUrl` command, optionally with ?play=
    naming an audio file (in AUDIO_FOLDER) to autoplay immediately."""
    now = datetime.now()
    next_prayer, next_prayer_dt, today_times, _today_row, prev_prayer_dt = _compute_next_prayer(now)
    play_file = request.args.get("play") or None
    with _tv_now_playing_lock:
        current_play_id = _tv_now_playing["play_id"]
    with config_lock:
        current_cfg = load_config()
    weather = islamic_utils.get_weather(current_cfg.get("lat"), current_cfg.get("lon"))
    return render_template(
        "tv_display.html",
        cfg=current_cfg,
        today_times=today_times,
        next_prayer=next_prayer,
        next_prayer_iso=next_prayer_dt.isoformat() if next_prayer_dt else None,
        prev_prayer_iso=prev_prayer_dt.isoformat() if prev_prayer_dt else None,
        weather=weather,
        play_file=play_file,
        play_id=current_play_id,
    )


@app.route("/tv_status")
def tv_status():
    """Polled by the TV display page (every few seconds) so it can start
    playing a new azan/dua on its own - this is what lets a plain app/tab
    that's just sitting on /tv-display react to a scheduled event, with no
    remote-control mechanism (like Fully Kiosk's REST API) required at all."""
    with _tv_now_playing_lock:
        return dict(_tv_now_playing)


@app.route("/audio_file/<path:filename>")
def serve_audio_file(filename):
    """Raw audio bytes for the TV display's <audio> tag - send_from_directory
    already guards against path traversal (e.g. ../../etc/passwd)."""
    return send_from_directory(AUDIO_FOLDER, filename)


# ----------------- plain-HTTP mirror, TV routes only -----------------
# Android WebView's fetch()/XHR calls don't honor WebViewClient's
# onReceivedSslError override the way page navigation does - the main
# /tv-display page itself loads fine over the site's self-signed HTTPS, but
# its JS polling of /tv_status silently fails its TLS handshake instead of
# ever reaching the server, so a scheduled azan would play on the main
# speaker but never show up on the TV. The TV display never actually needs
# HTTPS (that's only required elsewhere for the browser Geolocation API),
# so it's mirrored here on a separate plain-HTTP port instead of requiring
# every TV/Fire TV device to trust a certificate. Only these three routes
# are exposed this way - everything else (settings, Wi-Fi, uploads, etc.)
# stays HTTPS-only on the main port.
tv_http_app = Flask(__name__ + ".tv_http")
tv_http_app.add_url_rule("/tv-display", view_func=tv_display)
tv_http_app.add_url_rule("/tv_status", view_func=tv_status)
tv_http_app.add_url_rule("/audio_file/<path:filename>", view_func=serve_audio_file)


def _run_tv_http_mirror():
    from wsgiref.simple_server import make_server, WSGIRequestHandler

    class _QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass  # this gets polled every few seconds - keep it quiet

    with config_lock:
        port = load_config().get("tv_http_port", 5051)
    try:
        httpd = make_server("0.0.0.0", port, tv_http_app, handler_class=_QuietHandler)
        print(f"[TV] plain-HTTP mirror listening on :{port}")
        httpd.serve_forever()
    except Exception as e:
        print(f"[TV] plain-HTTP mirror failed to start on :{port}: {e}")


threading.Thread(target=_run_tv_http_mirror, daemon=True).start()


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
