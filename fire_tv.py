"""Fire TV / Fully Kiosk Browser integration.

Fully Kiosk Browser (sideloaded on the Fire TV, Remote Admin enabled) exposes
a local REST API on port 2323: http://<device-ip>:2323/?cmd=<command>&password=<pw>
Reference commands used here: screenOn, stopScreensaver, toForeground, loadUrl.
(https://www.fully-kiosk.com/en/ - Remote Admin / REST interface)

This module only ever wakes the TV and points it at our own /tv-display page -
it never reaches into Fully Kiosk's settings or anything else.
"""
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

_REQUEST_TIMEOUT = 3
_ADB_TIMEOUT = 10

# Standard Android keycodes - not Fire-TV-specific, work on any adb-reachable
# Android/Fire OS device.
_KEYCODE_WAKEUP = "224"
_KEYCODE_SLEEP = "223"

# The TV app doesn't find out the instant azan audio stops on the speaker -
# AzanOverlayService only polls /tv_status every 5s, then has to load the
# WebView, and is designed to stay up for a while so it's actually seen
# (its own auto-dismiss is 6 minutes). Sleeping the screen the moment the
# speaker finishes would cut that off before it ever properly showed -
# give it this much extra time awake first.
_POST_AZAN_BUFFER_S = 90


def _lan_ip():
    """Best-effort LAN IP for this machine, so the Fire TV (a separate
    device) can actually reach the /tv-display URL we hand it - 127.0.0.1
    or localhost would only mean something to this Pi itself."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"


def _fk_command(base_url, password, cmd, extra=None):
    params = {"cmd": cmd, "password": password}
    if extra:
        params.update(extra)
    url = base_url.rstrip("/") + "/?" + urllib.parse.urlencode(params)
    try:
        urllib.request.urlopen(url, timeout=_REQUEST_TIMEOUT)
    except Exception as e:
        print(f"[FireTV] '{cmd}' failed: {e}")


def notify_display(cfg, audio_filename=None):
    """Best-effort, fire-and-forget: wake the Fire TV, dismiss its
    screensaver, bring Fully Kiosk to the foreground (taking over from
    whatever else is currently showing), and load our TV display page -
    optionally with a specific audio file for that page to autoplay.

    Always runs in a background thread and never raises - this is a
    nice-to-have display sync, not something that should be able to delay
    or break the actual azan playback on the main speaker if the Fire TV
    or network happens to be slow/unreachable.
    """
    if not cfg.get("tv_display_enabled"):
        return
    base_url = (cfg.get("fully_kiosk_url") or "").strip()
    if not base_url:
        return
    password = cfg.get("fully_kiosk_password") or ""
    # The plain-HTTP mirror (app.py's tv_http_app), not the main HTTPS port -
    # Fully Kiosk (and any other WebView-based viewer) polling this page's
    # /tv_status over the main site's self-signed cert can silently fail its
    # TLS handshake even though the page itself loads fine, since fetch()/XHR
    # doesn't go through the same "ignore SSL errors" path as navigation.
    # Plain HTTP sidesteps that entirely - this page never needed HTTPS
    # anyway (that's only for the Geolocation API used elsewhere).
    port = cfg.get("tv_http_port", 5051)

    def _run():
        display_url = f"http://{_lan_ip()}:{port}/tv-display"
        if audio_filename:
            display_url += "?play=" + urllib.parse.quote(audio_filename)
        _fk_command(base_url, password, "screenOn")
        _fk_command(base_url, password, "stopScreensaver")
        _fk_command(base_url, password, "toForeground")
        _fk_command(base_url, password, "loadUrl", {"url": display_url})

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------
# ADB-based wake/sleep for a Fire TV running our own native TV app
# (com.smartazan.tvdisplay) rather than Fully Kiosk. Fire TV Cube typically
# has HDMI-CEC linked to the actual TV/soundbar, so waking the Fire TV device
# itself also powers the screen on; sleeping it powers the screen back off.
# ---------------------------------------------------------------------

def _adb(ip, *args):
    """Best-effort adb command against <ip>:5555 - reconnects first since a
    TCP adb session doesn't survive a reboot of either side. Never raises:
    an unreachable/unauthorized device should just mean "did nothing", not
    a crash of whatever azan/dua flow called this."""
    target = f"{ip}:5555"
    try:
        subprocess.run(["adb", "connect", target], capture_output=True,
                        text=True, timeout=_ADB_TIMEOUT)
        return subprocess.run(["adb", "-s", target] + list(args),
                               capture_output=True, text=True, timeout=_ADB_TIMEOUT)
    except Exception as e:
        print(f"[FireTV ADB] {' '.join(args)} failed: {e}")
        return None


def _adb_is_awake(ip):
    r = _adb(ip, "shell", "dumpsys", "power")
    return bool(r) and "mWakefulness=Awake" in (r.stdout or "")


def _parse_tv_ips(cfg):
    """adb_tv_ips is stored as a single comma/whitespace-separated string
    (multiple Fire TVs, each running the Smart Azan TV app) - accepts a
    list too, and falls back to the older single-IP adb_tv_ip field."""
    raw = cfg.get("adb_tv_ips")
    if raw is None:
        raw = cfg.get("adb_tv_ip")
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[,\s]+", str(raw or ""))
    return [p.strip() for p in parts if p.strip()]


def run_adb_tv_cycle(cfg, finished_event):
    """If enabled, wake every configured Fire TV that's currently asleep for
    azan; once finished_event is set (azan playback on the main speaker has
    ended), put each one back to sleep - but only the ones *this* call
    actually woke, so a TV someone is actually watching is never turned off
    out from under them. Meant to be run in its own background thread,
    decoupled from the actual azan audio timing - adb round-trips (network +
    HDMI wake time), for however many TVs are configured, must never be able
    to delay the azan itself."""
    if not cfg.get("adb_tv_enabled"):
        return
    ips = _parse_tv_ips(cfg)
    if not ips:
        return

    woke = {}
    for ip in ips:
        woke[ip] = not _adb_is_awake(ip)
        if woke[ip]:
            _adb(ip, "shell", "input", "keyevent", _KEYCODE_WAKEUP)

    finished_event.wait(timeout=1800)  # safety cap - never wait forever

    if any(woke.values()):
        time.sleep(_POST_AZAN_BUFFER_S)

    for ip in ips:
        if woke.get(ip):
            _adb(ip, "shell", "input", "keyevent", _KEYCODE_SLEEP)
