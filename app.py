#!/usr/bin/env python3
import os
import json
import csv
import threading
import time
from datetime import datetime

from flask import Flask, render_template

# local modules
import wifi
import bluetooth
import routes_azan
import audio_player

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
            "friday_dua": {"file": "", "time": ""},
            "iqama_audio": "iqama.mp3",
            "output_device": "auto",
            "audio_output_mode": "auto",
            "alsa_device": "",
            "bluetooth_mac": None,
            "bluetooth_sink": None,
            "volume": 70,
            "hotspot_ssid": "SmartAzanPi",
            "hotspot_password": "changeme123",
            "hotspot_enabled": False,
            "auto_hotspot_enabled": False,
            "wifi_autoconnect": True,
            "preferred_wifi_ssid": "",
            "wifi_networks": {},
            "after_azan_dua": "",
            "port": 5050,
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
    cfg.setdefault("output_device", "auto")
    cfg.setdefault("audio_output_mode", cfg.get("output_device", "auto"))
    cfg.setdefault("alsa_device", "")
    cfg.setdefault("volume", 70)
    cfg.setdefault("port", 5050)
    cfg.setdefault("wifi_networks", {})
    cfg.setdefault("wifi_autoconnect", True)
    cfg.setdefault("after_azan_dua", "")

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
def _bluetooth_autoconnect_loop():
    while True:
        with config_lock:
            c = load_config()
        mac = c.get("bluetooth_mac")
        mode = (c.get("audio_output_mode") or "auto").lower()
        if mac and mode in ("bluetooth", "auto") and not bluetooth.is_connected(mac):
            print(f"[Bluetooth] {mac} not connected, attempting reconnect…")
            bluetooth.ensure_bluetooth_ready()
            bluetooth.run_bluetoothctl_cmd(["connect", mac])
        time.sleep(30)


threading.Thread(target=_bluetooth_autoconnect_loop, daemon=True).start()


# ----------------- audio for scheduler -----------------
def play_audio(filename):
    path = os.path.join(AUDIO_FOLDER, filename)
    with config_lock:
        cfg_now = load_config()
    audio_player.play(path, cfg_now)


# ----------------- scheduler thread -----------------
def scheduler():
    print("[Scheduler] Running…")
    played_events = set()
    last_min = -1
    while True:
        now = datetime.now()
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
                                            play_audio(azan_file)
                                            played_events.add(eid)

                                            # after-azan dua
                                            after_dua = (current_cfg.get("after_azan_dua") or "").strip()
                                            if after_dua:
                                                print(f"[Scheduler] Playing after-azan dua ({after_dua})")
                                                play_audio(after_dua)
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
                                            play_audio(iq_file)
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
                    play_audio(file_)
                    played_events.add(eid)

        # --- 3) Friday special ---
        friday_dua = current_cfg.get("friday_dua") or {}
        if today_name == "friday":
            ffile = (friday_dua.get("file") or "").strip()
            ftime = (friday_dua.get("time") or "").strip()
            if ffile and ftime == now.strftime("%H:%M"):
                eid = f"friday_{current_minute_str}"
                if eid not in played_events:
                    print(f"[Scheduler] Playing Friday dua {ffile}")
                    play_audio(ffile)
                    played_events.add(eid)

        time.sleep(10)


# ----------------- index -----------------
@app.route("/")
def index():
    today_str = datetime.now().strftime("%d/%m/%Y")
    timetable = []
    if os.path.exists(TIMETABLE_FILE):
        try:
            with open(TIMETABLE_FILE) as csvfile:
                for row in csv.DictReader(csvfile):
                    if (row.get("Date") or "").strip() == today_str:
                        timetable.append(row)
                        break
        except Exception as e:
            print("[Index] timetable read error:", e)

    with config_lock:
        current_cfg = load_config()

    bt_devices = bluetooth.get_scanned_devices()

    return render_template(
        "index.html",
        cfg=current_cfg,
        timetable=timetable,
        bt_devices=bt_devices,
        current_date=today_str,
        now=datetime.now(),
    )


# ----------------- main -----------------
if __name__ == "__main__":
    threading.Thread(target=scheduler, daemon=True).start()

    # read port from config
    with config_lock:
        port = int(load_config().get("port", 5050))

    app.run(host="0.0.0.0", port=port)
