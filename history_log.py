#!/usr/bin/env python3
"""Append-only log of azan/iqama/dua playback events, for the Logs & History
page. Kept as JSON Lines so appending never requires rewriting the file."""
import json
import os
import threading
from datetime import datetime

LOG_FILE = "history.jsonl"
MAX_ENTRIES = 2000  # trimmed back to this many on write once exceeded

_lock = threading.Lock()


def log_event(event_type, label, filename, success, when=None):
    entry = {
        "ts": when or datetime.now().isoformat(timespec="seconds"),
        "type": event_type,
        "label": label,
        "file": filename,
        "success": bool(success),
    }
    with _lock:
        try:
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"[History] write error: {e}")
            return
        _trim_if_needed()


def _trim_if_needed():
    if not os.path.exists(LOG_FILE):
        return
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
    except Exception:
        return
    if len(lines) > MAX_ENTRIES * 1.2:
        with open(LOG_FILE, "w") as f:
            f.writelines(lines[-MAX_ENTRIES:])


def get_recent(limit=100):
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
    except Exception:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
