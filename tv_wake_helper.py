#!/usr/bin/env python3
"""Tiny standalone HTTP service exposing Fire TV wake/sleep control over the
LAN - after the main smart-azan app moved to a NUC on a different subnet
that can't reach these TVs directly (adb needs direct LAN access), this is
the one small piece that stays on this Pi (same LAN as the TVs), reachable
over Tailscale from the NUC.

Deliberately stdlib-only (no Flask/gunicorn/venv) - this has zero dependency
footprint, matching the whole point of moving the heavy app off this Pi.

POST /wake  - start waking every configured TV that's asleep
POST /sleep - signal that azan playback has finished, so each TV this call
              woke goes back to sleep after its post-azan buffer
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fire_tv

_lock = threading.Lock()
_finished_event = None

CFG = {
    "adb_tv_enabled": os.environ.get("ADB_TV_ENABLED", "true").lower() == "true",
    "adb_tv_ips": os.environ.get("ADB_TV_IPS", "192.168.50.129, 192.168.50.172"),
}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        global _finished_event
        if self.path == "/wake":
            with _lock:
                _finished_event = threading.Event()
                ev = _finished_event
            threading.Thread(
                target=fire_tv.run_adb_tv_cycle, args=(CFG, ev), daemon=True
            ).start()
            self._reply(200, {"ok": True})
        elif self.path == "/sleep":
            with _lock:
                ev = _finished_event
            if ev:
                ev.set()
            self._reply(200, {"ok": True})
        else:
            self._reply(404, {"ok": False})

    def log_message(self, format, *args):
        print(f"[TVWake] {self.address_string()} - {format % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5199"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[TVWake] listening on :{port}, TVs: {CFG['adb_tv_ips']}")
    server.serve_forever()
