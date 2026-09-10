"""Fire TV / Fully Kiosk Browser integration.

Fully Kiosk Browser (sideloaded on the Fire TV, Remote Admin enabled) exposes
a local REST API on port 2323: http://<device-ip>:2323/?cmd=<command>&password=<pw>
Reference commands used here: screenOn, stopScreensaver, toForeground, loadUrl.
(https://www.fully-kiosk.com/en/ - Remote Admin / REST interface)

This module only ever wakes the TV and points it at our own /tv-display page -
it never reaches into Fully Kiosk's settings or anything else.
"""
import socket
import threading
import urllib.parse
import urllib.request

_REQUEST_TIMEOUT = 3


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
    port = cfg.get("port", 5050)

    def _run():
        display_url = f"https://{_lan_ip()}:{port}/tv-display"
        if audio_filename:
            display_url += "?play=" + urllib.parse.quote(audio_filename)
        _fk_command(base_url, password, "screenOn")
        _fk_command(base_url, password, "stopScreensaver")
        _fk_command(base_url, password, "toForeground")
        _fk_command(base_url, password, "loadUrl", {"url": display_url})

    threading.Thread(target=_run, daemon=True).start()
