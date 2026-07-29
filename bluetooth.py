#!/usr/bin/env python3
import threading
import re
import subprocess
from flask import Blueprint, jsonify, request

bp = Blueprint("bluetooth", __name__)

_config_lock = None
_load_config = None
_save_config = None

# runtime state
bluetooth_scanning_enabled = False
bluetooth_devices = []
bluetooth_lock = threading.Lock()

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def init(app, config_lock, load_config, save_config):
    global _config_lock, _load_config, _save_config
    _config_lock = config_lock
    _load_config = load_config
    _save_config = save_config
    app.register_blueprint(bp)


# ---------------------------------------------------------------------
# low-level helpers
# ---------------------------------------------------------------------

def _run_btctl_script(lines, timeout=15):
    """
    Run 'bluetoothctl' with several lines.
    We FIRST turn agent off, THEN set our agent, because your log shows:
    'Failed to register agent object'
    """
    # make sure bluetoothd is up
    try:
        subprocess.run(["systemctl", "is-active", "--quiet", "bluetooth"], check=True)
    except subprocess.CalledProcessError:
        try:
            subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
        except Exception as e:
            print("[Bluetooth] failed to start bluetoothd:", e)

    # we prepend 'agent off' so we can safely register again
    all_lines = ["agent off"] + list(lines) + ["quit"]
    script = "\n".join(all_lines)

    try:
        p = subprocess.run(
            ["bluetoothctl"],
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        out = (p.stdout or "") + (p.stderr or "")
        if out.strip():
            print("[bluetoothctl]", out.strip())
        return out
    except Exception as e:
        print("[bluetoothctl] script error:", e)
        return ""


def _run_btctl_cmd(args, timeout=15):
    """
    Single bluetoothctl command.
    We'll also do the 'waiting to connect to bluetoothd...' case.
    """
    # make sure bluetoothd is up
    try:
        subprocess.run(["systemctl", "is-active", "--quiet", "bluetooth"], check=True)
    except subprocess.CalledProcessError:
        try:
            subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
        except Exception as e:
            print("[Bluetooth] failed to start:", e)

    try:
        p = subprocess.run(
            ["bluetoothctl"] + list(args),
            text=True,
            input="\n",
            capture_output=True,
            timeout=timeout,
        )
        out = (p.stdout or "") + (p.stderr or "")
        if out.strip():
            print("[bluetoothctl]", out.strip())
        return p.returncode == 0
    except Exception as e:
        print("[bluetoothctl] error:", e)
        return False


# IMPORTANT: this is the name your old routes expect
def run_bluetoothctl_cmd(args):
    """compat wrapper for routes_azan.py"""
    return _run_btctl_cmd(args)


def ensure_bluetooth_ready():
    """
    Prepare controller: power on + agent + default-agent.
    We do 'agent off' first so we don't get 'Failed to register agent object'.
    """
    _run_btctl_script([
        "power on",
        "agent NoInputNoOutput",
        "default-agent",
    ])


def _parse_devices_output(txt):
    items = []
    for line in (txt or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 2)
        if len(parts) >= 3 and parts[0].lower() == "device":
            mac = parts[1].strip()
            name = parts[2].strip()
            items.append({"mac": mac, "name": name})
    return items


def _bt_info(mac):
    """
    Return info text for device
    """
    return _run_btctl_script([f"info {mac}"])


def is_connected(mac):
    if not mac:
        return False
    return "Connected: yes" in _bt_info(mac)


def _force_pair_and_connect(mac):
    """
    Tries the reliable sequence for Alexa/Echo:
    - stop scanning (UI may still be polling)
    - ensure controller ready
    - remove old device
    - pair
    - trust
    - connect (multiple tries, short sleep)
    Returns True on success.
    """
    import time

    # stop UI scan flag so we don't keep discovering during connect
    global bluetooth_scanning_enabled
    with bluetooth_lock:
        bluetooth_scanning_enabled = False

    ensure_bluetooth_ready()

    # remove old record (this avoids "Already exists" + some busy states)
    _run_btctl_cmd(["remove", mac])

    # pair (this will fail if Echo is NOT in pairing mode)
    _run_btctl_cmd(["pair", mac])

    # trust anyway
    _run_btctl_cmd(["trust", mac])

    # try to connect a few times – Echo sometimes needs 2-3 attempts
    for _ in range(4):
        ok = _run_btctl_cmd(["connect", mac])
        if ok:
            return True
        time.sleep(2.5)

    return False



# ---------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------

@bp.route("/bt_state")
def bt_state():
    # show paired, trusted, connected
    paired_txt = _run_btctl_script(["paired-devices"])
    paired = _parse_devices_output(paired_txt)

    all_txt = _run_btctl_script(["devices"])
    all_devs = _parse_devices_output(all_txt)

    trusted = []
    connected = []

    for d in all_devs:
        info = _bt_info(d["mac"])
        if "Trusted: yes" in info:
            trusted.append(d)
        if "Connected: yes" in info:
            connected.append(d)

    with bluetooth_lock:
        scanning = bluetooth_scanning_enabled

    return jsonify({
        "scanning": scanning,
        "paired": paired,
        "trusted": trusted,
        "connected": connected,
        "all": all_devs,
    })


@bp.route("/bt_scan", methods=["POST"])
def bt_scan():
    global bluetooth_scanning_enabled
    with bluetooth_lock:
        if bluetooth_scanning_enabled:
            return jsonify({"status": "already_scanning", "devices": bluetooth_devices})
        bluetooth_scanning_enabled = True
        bluetooth_devices.clear()

    def do_scan():
        global bluetooth_scanning_enabled
        ensure_bluetooth_ready()
        try:
            # 10s active scan
            subprocess.run(
                ["bluetoothctl", "--timeout", "10", "scan", "on"],
                text=True,
                capture_output=True,
            )
            out = subprocess.run(
                ["bluetoothctl", "devices"],
                text=True,
                capture_output=True,
            ).stdout
            found = _parse_devices_output(out or "")
            with bluetooth_lock:
                bluetooth_devices.extend(found)
            print("[Bluetooth] found:", bluetooth_devices)
        except Exception as e:
            print("[Bluetooth] scan error:", e)
        finally:
            with bluetooth_lock:
                bluetooth_scanning_enabled = False

    threading.Thread(target=do_scan, daemon=True).start()
    return jsonify({"status": "started"})


@bp.route("/bt_scan_stop", methods=["POST"])
def bt_scan_stop():
    global bluetooth_scanning_enabled
    with bluetooth_lock:
        bluetooth_scanning_enabled = False
    # can't stop the real bluetoothctl, but this lets the UI stop polling
    return jsonify({"ok": True})


# ... previous code above ...

@bp.route("/bt_devices")
def bt_devices_api():
    with bluetooth_lock:
        return jsonify({"devices": bluetooth_devices})


def get_scanned_devices():
    """Return the last Bluetooth scan results as a plain Python list."""
    with bluetooth_lock:
        return list(bluetooth_devices)





@bp.route("/bt_trust", methods=["POST"])
def bt_trust():
    mac = (request.form.get("mac") or "").strip()
    if not MAC_RE.match(mac):
        return jsonify({"ok": False, "error": "bad mac"}), 400
    ensure_bluetooth_ready()
    ok = _run_btctl_cmd(["trust", mac])
    return jsonify({"ok": ok})


@bp.route("/bt_untrust", methods=["POST"])
def bt_untrust():
    mac = (request.form.get("mac") or "").strip()
    if not MAC_RE.match(mac):
        return jsonify({"ok": False, "error": "bad mac"}), 400
    ensure_bluetooth_ready()
    ok = _run_btctl_cmd(["untrust", mac])
    return jsonify({"ok": ok})


@bp.route("/bt_disconnect", methods=["POST"])
def bt_disconnect():
    mac = (request.form.get("mac") or "").strip()
    if not MAC_RE.match(mac):
        return jsonify({"ok": False, "error": "bad mac"}), 400
    ensure_bluetooth_ready()
    ok = _run_btctl_cmd(["disconnect", mac])
    return jsonify({"ok": ok})


@bp.route("/bt_force_connect", methods=["POST"])
def bt_force_connect():
    mac = (request.form.get("mac") or "").strip()
    if not MAC_RE.match(mac):
        return jsonify({"ok": False, "error": "bad mac"}), 400
    ok = _force_pair_and_connect(mac)
    return jsonify({"ok": ok})
