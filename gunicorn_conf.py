"""gunicorn config for Smart Azan.

workers=1 is required, not optional - app.py starts several background
threads (scheduler, Bluetooth auto-reconnect, mosque timetable auto-sync,
Wi-Fi monitor) at import time. Multiple gunicorn workers would each fork
their own copy of the whole process, meaning multiple scheduler loops all
racing to play the same azan/dua/khutbah at once. threads=N instead gives
real concurrency for HTTP requests within that single worker, via
gthread's real OS threads.

Port and HTTPS cert are read from the same config.json / cert.pem/cert.key
files app.py itself uses, so this stays in sync however those are set.
"""
import json
import os

_here = os.path.dirname(os.path.abspath(__file__))


def _load_port():
    try:
        with open(os.path.join(_here, "config.json")) as f:
            return int(json.load(f).get("port", 5050))
    except Exception:
        return 5050


workers = 1
worker_class = "gthread"
threads = 8
timeout = 1800  # generous - a scheduled azan/Quran play can legitimately run several minutes
graceful_timeout = 30
bind = f"0.0.0.0:{_load_port()}"

_cert = os.path.join(_here, "cert.pem")
_key = os.path.join(_here, "cert.key")
if os.path.exists(_cert) and os.path.exists(_key):
    certfile = _cert
    keyfile = _key
