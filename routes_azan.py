#!/usr/bin/env python3
import os
import re
import csv
import subprocess
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, send_from_directory
)

# your local modules
import wifi
import bluetooth
import audio_player
import history_log

bp = Blueprint("azan", __name__)

# injected from app.py
_config_lock = None
_load_config = None
_save_config = None
_AUDIO_FOLDER = None
_TIMETABLE_FILE = None
_STATIC_FOLDER = None

# ----------------- helpers -----------------

_ALLOWED_DAYS = {
    "daily", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday"
}

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def _normalize_day(v: str) -> str:
    v = (v or "daily").strip().lower()
    return v if v in _ALLOWED_DAYS else "daily"


def _normalize_mac(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    v = v.upper()
    if ":" not in v and len(v) == 12:
        # AABBCCDDEEFF -> AA:BB:CC:DD:EE:FF
        v = ":".join(v[i:i+2] for i in range(0, 12, 2))
    if MAC_RE.match(v):
        return v
    return ""


def _ensure_audio_folder():
    if _AUDIO_FOLDER and not os.path.exists(_AUDIO_FOLDER):
        os.makedirs(_AUDIO_FOLDER, exist_ok=True)


def _play_audio(filename: str, event_type: str = "manual", label: str = None):
    _ensure_audio_folder()
    path = os.path.join(_AUDIO_FOLDER, filename)
    with _config_lock:
        cfg = _load_config()
    ok = audio_player.play(path, cfg)
    history_log.log_event(event_type, label or event_type, filename, ok)
    return ok


def _bluez_card_name_for_mac(mac):
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "cards"],
            capture_output=True, text=True, check=True
        ).stdout
    except Exception as e:
        print(f"[Audio] list cards error: {e}")
        return None
    target = mac.upper().replace(":", "_")
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[1].startswith("bluez_card.") and target in parts[1].upper():
            return parts[1]
    return None


def _set_bt_card_profile(mac, profile="a2dp_sink"):
    card = _bluez_card_name_for_mac(mac)
    if not card:
        print(f"[Audio] No bluez_card for {mac} yet; profile not set.")
        return False
    try:
        subprocess.run(["pactl", "set-card-profile", card, profile], check=True)
        print(f"[Audio] Set {card} profile -> {profile}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[Audio] set-card-profile error: {e}")
        return False


def _set_system_volume(cfg, volume: int):
    audio_player.set_volume(cfg, volume)


# ---- Wi-Fi safe wrappers (your wifi.py doesn't have these names) ----

def _wifi_get_scanned_networks():
    # try several possibilities
    if hasattr(wifi, "get_scanned_networks"):
        try:
            return wifi.get_scanned_networks() or []
        except Exception:
            return []
    if hasattr(wifi, "SCAN_RESULTS"):
        return getattr(wifi, "SCAN_RESULTS") or []
    # fallback
    return []


def _wifi_is_scanning():
    if hasattr(wifi, "is_wifi_scanning"):
        try:
            return wifi.is_wifi_scanning()
        except Exception:
            return False
    if hasattr(wifi, "SCAN_RUNNING"):
        return bool(getattr(wifi, "SCAN_RUNNING"))
    return False


# ----------------- init -----------------

def init(app,
         config_lock,
         load_config,
         save_config,
         audio_folder="audio",
         timetable_file="timetable.csv",
         static_folder="static"):
    global _config_lock, _load_config, _save_config
    global _AUDIO_FOLDER, _TIMETABLE_FILE, _STATIC_FOLDER

    _config_lock = config_lock
    _load_config = load_config
    _save_config = save_config
    _AUDIO_FOLDER = audio_folder
    _TIMETABLE_FILE = timetable_file
    _STATIC_FOLDER = static_folder
    _ensure_audio_folder()

    # register once
    if "azan" not in app.blueprints:
        app.register_blueprint(bp)


# ----------------- views -----------------

@bp.route("/azan")
def azan_page():
    with _config_lock:
        current_cfg = _load_config()

    today = datetime.now().strftime("%d/%m/%Y")
    timetable_today = []
    if _TIMETABLE_FILE and os.path.exists(_TIMETABLE_FILE):
        try:
            with open(_TIMETABLE_FILE) as csvfile:
                for row in csv.DictReader(csvfile):
                    if row.get("Date") == today:
                        timetable_today.append(row)
                        break
        except Exception as e:
            print(f"[Azan] timetable read error: {e}")

    return render_template(
        "azan.html",
        cfg=current_cfg,
        timetable_today=timetable_today,
        current_date=today,
        now=datetime.now()
    )


@bp.route("/manual_play_azan", methods=["POST"])
def manual_play_azan():
    prayer = request.form.get("prayer")
    if prayer:
        with _config_lock:
            current_cfg = _load_config()
            audio_file = current_cfg["azan_audio_per_prayer"].get(
                prayer, current_cfg["azan_audio"]
            )
        _play_audio(audio_file, "manual", f"{prayer} azan (manual test)")
        flash(f"{prayer} azan played.", "success")
    return redirect(url_for("azan.azan_page"))





@bp.route("/upload_iqama_audio", methods=["POST"])
def upload_iqama_audio():
    f = request.files.get("iqama_file")
    if not f or f.filename == "":
        flash("No file selected for Iqama audio upload.", "danger")
        return redirect(url_for("azan.azan_page"))
    filename = f.filename
    try:
        _ensure_audio_folder()
        f.save(os.path.join(_AUDIO_FOLDER, filename))
        with _config_lock:
            cfg = _load_config()
            cfg["iqama_audio"] = filename
            _save_config(cfg)
        flash(f"Iqama audio set to '{filename}'.", "success")
    except Exception as e:
        flash(f"Failed to upload Iqama audio: {e}", "danger")
    return redirect(url_for("azan.azan_page"))
@bp.route("/upload_khutbah_audio", methods=["POST"])
def upload_khutbah_audio():
    f = request.files.get("khutbah_file")
    khutbah_time = (request.form.get("khutbah_time") or "").strip()
    if not f or f.filename == "":
        flash("No file selected for Khutbah audio upload.", "danger")
        return redirect(url_for("azan.azan_page"))
    filename = f.filename
    try:
        _ensure_audio_folder()
        f.save(os.path.join(_AUDIO_FOLDER, filename))
        with _config_lock:
            cfg = _load_config()
            fd = cfg.get("friday_dua") or {}
            fd["khutbah_file"] = filename
            if khutbah_time:
                fd["khutbah_time"] = khutbah_time
            cfg["friday_dua"] = fd
            _save_config(cfg)
        flash(f"Khutbah audio set to '{filename}' at {khutbah_time or fd.get('khutbah_time', '-')}.", "success")
    except Exception as e:
        flash(f"Failed to upload Khutbah audio: {e}", "danger")
    return redirect(url_for("azan.azan_page"))


@bp.route("/upload_after_azan_dua", methods=["POST"])
def upload_after_azan_dua():
    f = request.files.get("file")
    if not f or f.filename == "":
        flash("No file selected for 'dua after azan'.", "danger")
        return redirect(url_for("azan.azan_page"))

    filename = f.filename
    try:
        # make sure audio folder exists
        if _AUDIO_FOLDER and not os.path.exists(_AUDIO_FOLDER):
            os.makedirs(_AUDIO_FOLDER, exist_ok=True)

        # save file
        f.save(os.path.join(_AUDIO_FOLDER, filename))

        # write to config
        with _config_lock:
            cfg = _load_config()
            cfg["after_azan_dua"] = filename
            _save_config(cfg)

        flash(f"'Dua after azan' set to {filename}.", "success")
    except Exception as e:
        flash(f"Failed to upload dua after azan: {e}", "danger")

    return redirect(url_for("azan.azan_page"))


@bp.route("/upload_csv", methods=["POST"])
def upload_csv():
    f = request.files.get("file")
    if not f:
        flash("No file selected.", "danger")
        return redirect(url_for("azan.azan_page"))
    try:
        f.save(_TIMETABLE_FILE)
        flash("Timetable uploaded.", "success")
    except Exception as e:
        flash(f"Upload failed: {e}", "danger")
    return redirect(url_for("azan.azan_page"))


@bp.route("/upload_azan_per_prayer", methods=["POST"])
def upload_azan_per_prayer():
    f = request.files.get("file")
    prayer = request.form.get("prayer")
    if not (f and prayer):
        flash("Missing file or prayer.", "danger")
        return redirect(url_for("azan.azan_page"))
    try:
        filename = f.filename
        _ensure_audio_folder()
        f.save(os.path.join(_AUDIO_FOLDER, filename))
        with _config_lock:
            cfg = _load_config()
            cfg["azan_audio_per_prayer"][prayer] = filename
            _save_config(cfg)
        flash(f"{prayer} azan set.", "success")
    except Exception as e:
        flash(f"Failed to upload {prayer} azan: {e}", "danger")
    return redirect(url_for("azan.azan_page"))


@bp.route("/upload_dua", methods=["POST"])
def upload_dua():
    f = request.files.get("file")
    dua_time = request.form.get("time")
    day = _normalize_day(request.form.get("day"))
    if not (f and dua_time):
        flash("Missing file or time.", "danger")
        return redirect(url_for("azan.azan_page"))
    filename = f.filename
    try:
        _ensure_audio_folder()
        f.save(os.path.join(_AUDIO_FOLDER, filename))
        with _config_lock:
            cfg = _load_config()
            cfg.setdefault("duas", [])
            cfg["duas"].append({"file": filename, "time": dua_time, "day": day})
            _save_config(cfg)
        flash(f"Dua added for '{'Every day' if day=='daily' else day.capitalize()}' at {dua_time}.", "success")
    except Exception as e:
        flash(f"Failed to upload dua: {e}", "danger")
    return redirect(url_for("azan.azan_page"))


@bp.route("/update_dua", methods=["POST"])
def update_dua():
    try:
        idx = int(request.form.get("index", -1))
        day = _normalize_day(request.form.get("day"))
        dua_time = request.form.get("time")
        if idx < 0 or not dua_time:
            flash("Invalid update.", "danger")
            return redirect(url_for("azan.azan_page"))
        with _config_lock:
            cfg = _load_config()
            duas = cfg.get("duas", [])
            if idx >= len(duas):
                flash("Dua not found.", "danger")
                return redirect(url_for("azan.azan_page"))
            duas[idx]["time"] = dua_time
            duas[idx]["day"] = day
            _save_config(cfg)
        flash("Dua updated.", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "danger")
    return redirect(url_for("azan.azan_page"))


@bp.route("/delete_dua", methods=["POST"])
def delete_dua():
    idx = request.form.get("index", type=int)
    with _config_lock:
        cfg = _load_config()
        duas = cfg.get("duas", [])
        if idx is None or not (0 <= idx < len(duas)):
            flash("Invalid dua index.", "danger")
            return redirect(url_for("azan.azan_page"))
        rem = duas.pop(idx)
        fp = os.path.join(_AUDIO_FOLDER, rem.get("file", ""))
        if rem.get("file") and os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception as e:
                print(f"[Dua] remove file error: {e}")
        _save_config(cfg)
    flash("Dua deleted.", "success")
    return redirect(url_for("azan.azan_page"))


@bp.route("/upload_friday_dua", methods=["POST"])
def upload_friday_dua():
    f = request.files.get("file")
    dua_time = request.form.get("time")
    if not (f and dua_time):
        flash("Missing file or time.", "danger")
        return redirect(url_for("azan.azan_page"))
    filename = f.filename
    try:
        _ensure_audio_folder()
        f.save(os.path.join(_AUDIO_FOLDER, filename))
        with _config_lock:
            cfg = _load_config()
            cfg["friday_dua"] = {"file": filename, "time": dua_time}
            _save_config(cfg)
        flash("Friday dua set.", "success")
    except Exception as e:
        flash(f"Failed to upload Friday dua: {e}", "danger")
    return redirect(url_for("azan.azan_page"))


@bp.route("/delete_friday_dua", methods=["POST"])
def delete_friday_dua():
    with _config_lock:
        cfg = _load_config()
        fd = cfg.get("friday_dua", {})
        if fd.get("file"):
            fp = os.path.join(_AUDIO_FOLDER, fd["file"])
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception as e:
                    print(f"[FridayDua] rm error: {e}")
            cfg["friday_dua"] = {"file": "", "time": ""}
            _save_config(cfg)
            flash("Friday dua deleted.", "success")
        else:
            flash("No Friday dua set.", "info")
    return redirect(url_for("azan.azan_page"))


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        form_id = request.form.get("form_id", "")

        with _config_lock:
            cfg = _load_config()

            # 1) keep existing wifi stuff so we don't lose it
            old_wifi_networks = cfg.get("wifi_networks", {})
            old_preferred = cfg.get("preferred_wifi_ssid", "")
            old_autocon = cfg.get("wifi_autoconnect", True)

            if form_id == "port":
                port = request.form.get("port")
                if port:
                    cfg["port"] = int(port)

            elif form_id == "audio":
                output_device = request.form.get("output_device")
                if output_device:
                    cfg["output_device"] = output_device
                    cfg["audio_output_mode"] = output_device

                cfg["alsa_device"] = (request.form.get("alsa_device") or "").strip()

                bt_mac_raw = request.form.get("bluetooth_mac", "")
                norm = _normalize_mac(bt_mac_raw)
                cfg["bluetooth_mac"] = norm

                bt_sink = request.form.get("bluetooth_sink", "")
                cfg["bluetooth_sink"] = bt_sink

            elif form_id == "location":
                try:
                    cfg["lat"] = float(request.form.get("lat", 0))
                    cfg["lon"] = float(request.form.get("lon", 0))
                except (TypeError, ValueError):
                    flash("Invalid latitude/longitude.", "danger")

            elif form_id == "jumma":
                fd = cfg.get("friday_dua") or {}
                fd["khutbah_time"] = (request.form.get("khutbah_time") or "").strip()
                fd["time"] = (request.form.get("friday_dua_time") or fd.get("time") or "").strip()
                cfg["friday_dua"] = fd

            elif form_id == "wifi":  # <-- add this form in settings.html
                ssid = (request.form.get("preferred_wifi_ssid") or "").strip()
                pwd = (request.form.get("preferred_wifi_password") or "").strip()
                auto = request.form.get("wifi_autoconnect") == "on"

                if ssid:
                    old_preferred = ssid
                    if pwd:
                        old_wifi_networks[ssid] = pwd
                old_autocon = auto

            # put them back (even if form wasn't wifi)
            cfg["wifi_networks"] = old_wifi_networks
            cfg["preferred_wifi_ssid"] = old_preferred
            cfg["wifi_autoconnect"] = old_autocon

            _save_config(cfg)

        flash("Settings saved.", "success")
        return redirect(url_for("azan.settings"))

    # GET -> render
    with _config_lock:
        cfg = _load_config()

    audio_outputs = audio_player.list_outputs(cfg)
    bt_sinks = [o for o in audio_outputs["outputs"] if o["kind"] == "bluetooth"]
    alsa_devices = [o for o in audio_outputs["outputs"] if o["backend"] == "alsa"]
    scanned_networks = _wifi_get_scanned_networks()
    is_scanning = _wifi_is_scanning()

    return render_template(
        "settings.html",
        cfg=cfg,
        bt_sinks=bt_sinks,
        alsa_devices=alsa_devices,
        audio_outputs=audio_outputs,
        scanned_networks=scanned_networks,
        is_scanning=is_scanning,
    )

@bp.route("/set_volume", methods=["POST"])
def set_volume_ajax():
    try:
        vol = int(request.form.get("volume"))
        if not (0 <= vol <= 100):
            return jsonify({"status": "error", "message": "Volume out of range (0-100)"}), 400
        with _config_lock:
            cfg = _load_config()
            cfg["volume"] = vol
            _save_config(cfg)
        _set_system_volume(cfg, vol)
        return jsonify({"status": "success", "volume": vol})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/connect_bt_device", methods=["POST"])
def connect_bt_device():
    # 1) get MAC from form or from config
    mac = (request.form.get("mac") or "").strip()
    mac = _normalize_mac(mac)

    with _config_lock:
        cfg = _load_config()

    if not mac:
        mac = _normalize_mac(cfg.get("bluetooth_mac", ""))

    if not mac:
        flash("No valid Bluetooth MAC provided.", "danger")
        return redirect(url_for("azan.settings"))

    # 2) save MAC to config immediately (so /settings shows the new one)
    with _config_lock:
        c = _load_config()
        c["bluetooth_mac"] = mac
        _save_config(c)

    # 3) make sure controller is up (this may print 'Failed to register agent' – it's ok)
    if hasattr(bluetooth, "ensure_bluetooth_ready"):
        bluetooth.ensure_bluetooth_ready()

    ok = False

    # 4) try EXACTLY what you did by hand: simple connect first
    if hasattr(bluetooth, "run_bluetoothctl_cmd"):
        ok = bluetooth.run_bluetoothctl_cmd(["connect", mac])

        # 5) if simple connect failed, try trust + connect
        if not ok:
            bluetooth.run_bluetoothctl_cmd(["trust", mac])
            ok = bluetooth.run_bluetoothctl_cmd(["connect", mac])

    # 6) last resort: the heavy one (remove + pair + connect)
    if not ok and hasattr(bluetooth, "_force_pair_and_connect"):
        ok = bluetooth._force_pair_and_connect(mac)

    # 7) set volume if we finally connected
    if ok:
        try:
            with _config_lock:
                c = _load_config()
                vol = int(c.get("volume", 70))
            _set_system_volume(c, vol)
        except Exception as e:
            print("[BT] volume set failed:", e)
        flash(f"Connected to {mac}.", "success")
    else:
        flash(
            f"Saved MAC {mac} but could not connect. "
            "If it's an Echo/Alexa, say “Alexa, pair” and press Connect again.",
            "danger",
        )

    return redirect(url_for("azan.settings"))



@bp.route("/force_bt_profile", methods=["POST"])
def force_bt_profile():
    mac = (request.form.get("mac") or "").strip()
    mac = _normalize_mac(mac)
    if not mac:
        with _config_lock:
            c = _load_config()
            mac = _normalize_mac(c.get("bluetooth_mac", ""))

    if not mac:
        flash("No Bluetooth MAC configured.", "danger")
        return redirect(url_for("azan.settings"))

    ok = _set_bt_card_profile(mac, "a2dp_sink")
    flash(("Switched to A2DP." if ok else "Failed to switch A2DP."),
          "success" if ok else "danger")
    return redirect(url_for("azan.settings"))


@bp.route("/disconnect_bt_device", methods=["POST"])
def disconnect_bt_device():
    with _config_lock:
        c = _load_config()
        mac = _normalize_mac(c.get("bluetooth_mac", ""))

    if not mac:
        flash("No Bluetooth MAC configured.", "danger")
        return redirect(url_for("azan.settings"))

    if hasattr(bluetooth, "run_bluetoothctl_cmd"):
        if bluetooth.run_bluetoothctl_cmd(["disconnect", mac]):
            flash("Bluetooth disconnected.", "success")
        else:
            flash("Failed to disconnect.", "danger")
    else:
        flash("Bluetooth module missing disconnect helper.", "danger")

    return redirect(url_for("azan.settings"))


@bp.route("/audio_status")
def audio_status():
    try:
        with _config_lock:
            c = _load_config()
        info = audio_player.list_outputs(c)
        info["output_device"] = c.get("audio_output_mode")
        info["default_sink"] = info.pop("default_pulse_sink")
        info["resolved_sink"] = info.get("resolved_target")
    except Exception as e:
        print("[audio_status] error:", e)
        info = {"output_device": None, "resolved_sink": None, "default_sink": None}
    return jsonify(info)


@bp.route("/audio_devices")
def audio_devices():
    with _config_lock:
        c = _load_config()
    return jsonify(audio_player.list_outputs(c))


@bp.route("/audio_test", methods=["POST"])
def audio_test():
    with _config_lock:
        c = dict(_load_config())

    # allow testing a device before it's saved to config
    mode = request.form.get("audio_output_mode")
    alsa_device = request.form.get("alsa_device")
    bluetooth_sink = request.form.get("bluetooth_sink")
    if mode:
        c["audio_output_mode"] = mode
    if alsa_device is not None:
        c["alsa_device"] = alsa_device
    if bluetooth_sink is not None:
        c["bluetooth_sink"] = bluetooth_sink

    backend, target = audio_player.resolve_target(c)
    ok, err = audio_player.play_test_tone(backend, target)
    return jsonify({"ok": ok, "backend": backend, "target": target, "error": err})


def _nd_lines(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        return [ln for ln in out.strip().splitlines() if ln.strip()]
    except Exception:
        return []


def _nd_ipv4_for(dev):
    try:
        out = subprocess.run(
            ['ip', '-o', '-4', 'addr', 'show', 'dev', dev],
            capture_output=True, text=True, check=True
        ).stdout
        for ln in out.splitlines():
            parts = ln.split()
            for i, x in enumerate(parts):
                if x == "inet" and i + 1 < len(parts):
                    return parts[i + 1].split("/")[0]
    except Exception:
        pass
    return None


@bp.route("/net_detail")
def net_detail():
    # we won't call wifi.get_network_details() because in your log it doesn't exist
    ethernet = {"up": False, "device": None, "ip": None}
    wifi_state = {"state": None, "unavailable": False, "connected": False,
                  "ssid": None, "device": None, "ip": None, "signal": None}

    # hotspot info – guard missing func
    hs = {}
    if hasattr(wifi, "get_hotspot_status"):
        try:
            hs = wifi.get_hotspot_status()
        except Exception:
            hs = {}
    if not isinstance(hs, dict):
        hs = {}

    for line in _nd_lines(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE,CONNECTION', 'dev', 'status']):
        parts = line.split(':')
        if len(parts) >= 4:
            dev, typ, st, con = parts[0], parts[1], parts[2], parts[3]
            if typ == 'ethernet' and st == 'connected':
                ethernet.update({"up": True, "device": dev, "ip": _nd_ipv4_for(dev)})
            if typ in ('wifi', 'wlan'):
                wifi_state["device"] = dev
                if st == 'unavailable':
                    wifi_state["unavailable"] = True
                wifi_state["connected"] = (st == 'connected')
                if wifi_state["connected"] and not wifi_state.get("ip"):
                    wifi_state["ip"] = _nd_ipv4_for(dev)

    for ln in _nd_lines(['nmcli', 'radio', 'wifi']):
        v = ln.strip().lower()
        if v in ('enabled', 'disabled'):
            wifi_state["state"] = v
            break

    for ln in _nd_lines(['nmcli', '-t', '-f', 'IN-USE,SSID,SIGNAL', 'dev', 'wifi']):
        parts = ln.split(':')
        if len(parts) >= 3 and parts[0].strip() == '*':
            wifi_state["ssid"] = parts[1]
            wifi_state["signal"] = parts[2]
            break

    return jsonify({
        "ethernet": ethernet,
        "wifi": wifi_state,
        "hotspot": {"active": hs.get("active", False), "ip": hs.get("ip")}
    })


# ---- PWA assets ----

@bp.route('/manifest.json')
def pwa_manifest():
    return send_from_directory(_STATIC_FOLDER, 'manifest.json',
                               mimetype='application/manifest+json')


@bp.route('/sw.js')
def pwa_service_worker():
    return send_from_directory(_STATIC_FOLDER, 'sw.js',
                               mimetype='application/javascript')
