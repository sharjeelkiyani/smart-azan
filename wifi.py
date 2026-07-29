#!/usr/bin/env python3
import os
import re
import time
import threading
import subprocess
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

bp = Blueprint("wifi", __name__)

# will be injected from app.py
_config_lock = None
_load_config = None
_save_config = None
_CONFIG_PATH = None
_TIMETABLE_FILE = None

# runtime wifi state
wifi_scanning_enabled = False
scanned_wifi_networks = []
wifi_lock = threading.Lock()

HOTSPOT_CONNECTION_NAME = "SmartAzanHotspot"


def init(app, config_lock, load_config, save_config, config_path=None, timetable_file=None):
    """called from app.py"""
    global _config_lock, _load_config, _save_config, _CONFIG_PATH, _TIMETABLE_FILE
    _config_lock = config_lock
    _load_config = load_config
    _save_config = save_config
    _CONFIG_PATH = config_path
    _TIMETABLE_FILE = timetable_file

    app.register_blueprint(bp)
    print("[WiFi] blueprint registered")


def start_background_threads():
    t = threading.Thread(target=wifi_autoconnect_daemon, daemon=True)
    t.start()
    t2 = threading.Thread(target=monitor_network_and_hotspot, daemon=True)
    t2.start()
    print("[WiFi] background threads started")


# ---------------------------------------------------------------------
# core helpers
# ---------------------------------------------------------------------
def _detect_wifi_iface():
    """Detect Wi-Fi iface (works on Pi and NUC)."""
    env_iface = os.environ.get("WIFI_IFACE")
    if env_iface:
        return env_iface
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            capture_output=True, text=True, check=True
        ).stdout
        for ln in out.splitlines():
            parts = ln.split(":")
            if len(parts) >= 3 and parts[1] in ("wifi", "wlan"):
                return parts[0]
    except Exception as e:
        print("[WiFi] iface detect error:", e)
    return "wlan0"  # safe fallback


def scan_wifi_networks_background():
    """Scan and fill scanned_wifi_networks."""
    global scanned_wifi_networks, wifi_scanning_enabled
    print("[WiFi] Starting background scan…", flush=True)
    try:
        subprocess.run(["nmcli", "dev", "wifi", "rescan"], capture_output=True, text=True)
        time.sleep(2)

        cmd = [
            "nmcli", "-t", "-f",
            "SSID,BSSID,MODE,CHAN,RATE,SIGNAL,BARS,SECURITY",
            "dev", "wifi", "list"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        nets = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = re.split(r"(?<!\\):", line)
            if len(parts) >= 8:
                nets.append({
                    "ssid": parts[0] or "Hidden/Unknown SSID",
                    "bssid": parts[1].replace("\\:", ":"),
                    "mode": parts[2],
                    "channel": parts[3],
                    "rate": parts[4],
                    "signal": parts[5],
                    "bars": parts[6],
                    "security": parts[7],
                })
        with wifi_lock:
            scanned_wifi_networks = nets
        print(f"[WiFi] Found {len(nets)} networks.")
    except Exception as e:
        print("[WiFi] scan error:", e)
    finally:
        with wifi_lock:
            wifi_scanning_enabled = False
        print("[WiFi] Background scan finished.")


def connect_to_wifi_cmd(ssid, password=None):
    """
    Connect to Wi-Fi.
    RETURNS: (ok: bool, msg: str)  ← IMPORTANT!
    """
    iface = _detect_wifi_iface()
    wrapper = "/usr/local/bin/nmcli-wifi-connect"

    if os.path.exists(wrapper):
        # we prepared a sudo-whitelisted helper
        cmd = ["sudo", wrapper, ssid, password or "", iface]
    else:
        cmd = ["nmcli", "dev", "wifi", "connect", ssid, "ifname", iface]
        if password:
            cmd.extend(["password", password])

    print(f"[WiFi] Connecting to {ssid} on {iface} …")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()

        # always save what user entered
        with _config_lock:
            cfg = _load_config()
            cfg.setdefault("wifi_networks", {})
            cfg["wifi_networks"][ssid] = password or ""
            cfg["preferred_wifi_ssid"] = ssid
            cfg["wifi_autoconnect"] = True
            _save_config(cfg)

        if res.returncode == 0:
            print(f"[WiFi] Connected to {ssid}")
            return True, out or "connected"
        else:
            print(f"[WiFi] connect failed ({res.returncode}): {err or out}")
            return False, err or out or f"nmcli failed ({res.returncode})"
    except Exception as e:
        print("[WiFi] connect exception:", e)
        return False, str(e)


def start_hotspot(ssid, password):
    print(f"[Hotspot] start: {ssid}")
    iface = _detect_wifi_iface()
    try:
        subprocess.run(["nmcli", "con", "del", HOTSPOT_CONNECTION_NAME],
                       capture_output=True, text=True)
        subprocess.run([
            "nmcli", "con", "add",
            "type", "wifi",
            "ifname", iface,
            "mode", "ap",
            "con-name", HOTSPOT_CONNECTION_NAME,
            "ssid", ssid
        ], check=True)
        subprocess.run(["nmcli", "con", "modify", HOTSPOT_CONNECTION_NAME,
                        "wifi-sec.key-mgmt", "wpa-psk"], check=True)
        subprocess.run(["nmcli", "con", "modify", HOTSPOT_CONNECTION_NAME,
                        "wifi-sec.psk", password], check=True)
        subprocess.run(["nmcli", "con", "up", HOTSPOT_CONNECTION_NAME], check=True)
        return True
    except Exception as e:
        print("[Hotspot] start error:", e)
        return False


def stop_hotspot():
    print(f"[Hotspot] stop: {HOTSPOT_CONNECTION_NAME}")
    try:
        res = subprocess.run(["nmcli", "con", "down", HOTSPOT_CONNECTION_NAME],
                             capture_output=True, text=True)
        if res.returncode != 0 and "not active" in (res.stderr or "").lower():
            return True
        res.check_returncode()
        return True
    except Exception as e:
        print("[Hotspot] stop error:", e)
        return False


def get_hotspot_status():
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE,IP4.ADDRESS",
             "con", "show", "--active"],
            capture_output=True, text=True, check=True
        ).stdout
        for line in out.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 5 and parts[0] == HOTSPOT_CONNECTION_NAME and parts[3] == "activated":
                ip = parts[4].split("/")[0] if parts[4] else None
                return {"active": True, "ip": ip}
    except Exception:
        pass
    return {"active": False, "ip": None}


def is_internet_connected():
    try:
        subprocess.check_call(
            ["ping", "-c", "1", "-W", "1", "8.8.8.8"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except Exception:
        return False


def monitor_network_and_hotspot():
    print("[Network Monitor] start…")
    while True:
        with _config_lock:
            current = _load_config()
            auto_hotspot_enabled = current.get("auto_hotspot_enabled", False)
            ssid = current.get("hotspot_ssid", "SmartAzanPi")
            pw = current.get("hotspot_password", "changeme123")
            cfg_hotspot_enabled = current.get("hotspot_enabled", False)

        hs = get_hotspot_status()

        if auto_hotspot_enabled:
            if not is_internet_connected():
                if not hs["active"]:
                    if start_hotspot(ssid, pw):
                        with _config_lock:
                            c = _load_config()
                            c["hotspot_enabled"] = True
                            _save_config(c)
            else:
                if hs["active"]:
                    if stop_hotspot():
                        with _config_lock:
                            c = _load_config()
                            c["hotspot_enabled"] = False
                            _save_config(c)
        else:
            if hs["active"] and not cfg_hotspot_enabled:
                stop_hotspot()
            elif (not hs["active"]) and cfg_hotspot_enabled:
                start_hotspot(ssid, pw)

        time.sleep(15)


# ---------------------------------------------------------------------
# auto-wifi
# ---------------------------------------------------------------------
def _nm_saved_wifi_names():
    names = []
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"],
            capture_output=True, text=True, check=True
        ).stdout
        for ln in out.splitlines():
            parts = ln.split(":")
            if len(parts) >= 2 and parts[1] == "802-11-wireless":
                names.append(parts[0])
    except Exception:
        pass
    return names


def _nm_radio_state():
    try:
        out = subprocess.run(["nmcli", "radio", "wifi"],
                             capture_output=True, text=True, check=True).stdout.strip().lower()
        if out in ("enabled", "disabled"):
            return out
    except Exception:
        pass
    return None


def _nm_is_wifi_connected():
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev", "status"],
            capture_output=True, text=True, check=True
        ).stdout
        for ln in out.splitlines():
            p = ln.split(":")
            if len(p) >= 3 and p[1] in ("wifi", "wlan") and p[2] == "connected":
                return True
    except Exception:
        pass
    return False


def _nm_best_seen_saved():
    saved = set(_nm_saved_wifi_names())
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi"],
            capture_output=True, text=True, check=True
        ).stdout
        best = None
        for ln in out.splitlines():
            parts = ln.split(":")
            if len(parts) >= 2:
                ssid, sig = parts[0], parts[1] or "0"
                if ssid and ssid in saved:
                    try:
                        sigv = int(sig)
                    except:
                        sigv = 0
                    if best is None or sigv > best[1]:
                        best = (ssid, sigv)
        return best[0] if best else None
    except Exception:
        return None


def autoconnect_wifi_if_needed():
    with _config_lock:
        current = _load_config()
        auto = current.get("wifi_autoconnect", True)
        preferred = (current.get("preferred_wifi_ssid") or "").strip()
    if not auto:
        return

    if _nm_is_wifi_connected():
        return

    if _nm_radio_state() == "disabled":
        subprocess.run(["nmcli", "radio", "wifi", "on"], check=False)
        time.sleep(2)

    saved = set(_nm_saved_wifi_names())
    target = preferred if (preferred and preferred in saved) else None
    if not target:
        target = _nm_best_seen_saved()

    if target:
        subprocess.run(["nmcli", "con", "up", "id", target], check=False)
        time.sleep(5)


def wifi_autoconnect_daemon():
    print("[AutoWiFi] daemon start…")
    while True:
        try:
            autoconnect_wifi_if_needed()
        except Exception as e:
            print("[AutoWiFi] error:", e)
        time.sleep(20)


# ---------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------
@bp.route("/wifi")
def wifi_page():
    with wifi_lock:
        nets = scanned_wifi_networks.copy()
        is_scan = wifi_scanning_enabled
    with _config_lock:
        current_cfg = _load_config()
    hs = get_hotspot_status()
    return render_template("wifi.html",
                           scanned_networks=nets,
                           is_scanning=is_scan,
                           cfg=current_cfg,
                           hotspot_status=hs)


@bp.route("/scan_wifi", methods=["POST"])
def scan_wifi():
    global wifi_scanning_enabled
    with wifi_lock:
        if wifi_scanning_enabled:
            flash("Wi-Fi scan already in progress.", "info")
            return redirect(url_for("azan.settings"))  # 👈 back to settings
        wifi_scanning_enabled = True
        scanned_wifi_networks.clear()
    threading.Thread(target=scan_wifi_networks_background, daemon=True).start()
    flash("Wi-Fi scan started.", "success")
    return redirect(url_for("azan.settings"))  # 👈 stay on settings



@bp.route("/wifi_scan_status")
def wifi_scan_status():
    with wifi_lock:
        return jsonify({"is_scanning": wifi_scanning_enabled,
                        "networks": scanned_wifi_networks})


@bp.route("/connect_wifi", methods=["POST"])
def connect_wifi():
    ssid = (request.form.get("ssid") or "").strip()
    password = request.form.get("password") or ""
    if not ssid:
        flash("SSID required.", "danger")
        return redirect(url_for("wifi.wifi_page"))

    ok, msg = connect_to_wifi_cmd(ssid, password)

    if ok:
        flash(f"Connected to {ssid}.", "success")
    else:
        # important: we still saved to config even if nmcli failed
        flash(f"Saved Wi-Fi for {ssid}, but connect failed: {msg}", "warning")

    return redirect(url_for("wifi.wifi_page"))


@bp.route("/start_hotspot", methods=["POST"])
def start_hotspot_route():
    with _config_lock:
        current = _load_config()
        ssid = request.form.get("hotspot_ssid", current.get("hotspot_ssid", "SmartAzanPi"))
        pw = request.form.get("hotspot_password", current.get("hotspot_password", "changeme123"))
        current["hotspot_ssid"] = ssid
        current["hotspot_password"] = pw
        _save_config(current)

    if start_hotspot(ssid, pw):
        with _config_lock:
            c = _load_config()
            c["hotspot_enabled"] = True
            _save_config(c)
        flash("Hotspot started.", "success")
    else:
        flash("Failed to start hotspot.", "danger")
    return redirect(url_for("wifi.wifi_page"))


@bp.route("/stop_hotspot", methods=["POST"])
def stop_hotspot_route():
    if stop_hotspot():
        with _config_lock:
            c = _load_config()
            c["hotspot_enabled"] = False
            _save_config(c)
        flash("Hotspot stopped.", "success")
    else:
        flash("Failed to stop hotspot.", "danger")
    return redirect(url_for("wifi.wifi_page"))


@bp.route("/hotspot_status")
def hotspot_status_api():
    return jsonify(get_hotspot_status())


@bp.route("/api/wifi/radio", methods=["POST"])
def api_wifi_radio():
    data = request.get_json(silent=True) or {}
    state = (data.get('state') or '').lower()
    if state not in ('on', 'off'):
        return jsonify({"ok": False, "error": "state must be 'on' or 'off'"}), 400

    # check config – if hotspot is on or auto-hotspot is on, don't allow turning wifi OFF
    with _config_lock:
        cfg = _load_config()
        auto_hot = cfg.get("auto_hotspot_enabled", False)
        hs_enabled = cfg.get("hotspot_enabled", False)

    if state == "off" and (auto_hot or hs_enabled):
        # block it, tell frontend why
        return jsonify({
            "ok": False,
            "error": "Wi-Fi is needed for hotspot/auto-hotspot. Disable hotspot first."
        }), 400

    try:
        subprocess.run(['nmcli', 'radio', 'wifi', state], check=True)
        return jsonify({"ok": True, "state": "enabled" if state == "on" else "disabled"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

