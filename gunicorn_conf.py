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

# Bound the TLS handshake, in every gthread worker.
#
# gunicorn's gthread worker (TConn.init in gunicorn/workers/gthread.py) sets
# the accepted socket blocking with NO timeout, then calls do_handshake().
# A client that stalls mid-handshake - a phone that drops wifi/cell signal
# right after opening the HTTPS connection - leaves that worker thread
# blocked until Linux's own TCP retransmission timeout, which can be many
# minutes. With only `threads` worker slots (see above), a handful of
# stalled phones piling up is enough to freeze the whole app for everyone
# else - this is exactly what happened in production (see the
# "TimeoutError: [Errno 110] Connection timed out" in
# `journalctl -u smart-azan`, right before the service stopped responding).
#
# Patched here (not in the vendored gunicorn source) via gunicorn's own
# post_fork hook, so a `pip install -r requirements.txt` never overwrites it.
_HANDSHAKE_TIMEOUT = 15  # seconds - generous for a real handshake, tiny next to an OS-level hang


def post_fork(server, worker):
    from gunicorn.workers import gthread

    _orig_init = gthread.TConn.init

    def _bounded_init(self):
        if self.initialized or not self.cfg.is_ssl:
            return _orig_init(self)
        self.initialized = True
        self.sock.setblocking(True)
        if self.parser is None:
            self.sock.settimeout(_HANDSHAKE_TIMEOUT)
            try:
                self.sock = gthread.sock.ssl_wrap_socket(self.sock, self.cfg)
                if not self.cfg.do_handshake_on_connect:
                    self.sock.do_handshake()
            finally:
                self.sock.settimeout(None)  # back to plain blocking for the actual request

            if gthread.sock.is_http2_negotiated(self.sock):
                self.is_http2 = True
                self.parser = gthread.http.get_parser(
                    self.cfg, self.sock, self.client, http2_connection=True
                )
                self.parser.initiate_connection()
                return
            self.parser = gthread.http.get_parser(self.cfg, self.sock, self.client)

    gthread.TConn.init = _bounded_init
