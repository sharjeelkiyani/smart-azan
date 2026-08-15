#!/usr/bin/env python3
import csv
import os
import subprocess
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import audio_player
import bluetooth
import history_log
import islamic_utils

bp = Blueprint("dashboard", __name__)

_config_lock = None
_load_config = None
_save_config = None
_timetable_file = "timetable.csv"

PRAYER_NAMES = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]


def init(app, config_lock, load_config, save_config, timetable_file="timetable.csv"):
    global _config_lock, _load_config, _save_config, _timetable_file
    _config_lock = config_lock
    _load_config = load_config
    _save_config = save_config
    _timetable_file = timetable_file
    if "dashboard" not in app.blueprints:
        app.register_blueprint(bp)


def _cfg():
    with _config_lock:
        return _load_config()


def _save(cfg):
    with _config_lock:
        _save_config(cfg)


def _read_all_rows():
    if not os.path.exists(_timetable_file):
        return []
    try:
        with open(_timetable_file) as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"[Dashboard] timetable read error: {e}")
        return []


# ----------------- Prayer Times -----------------

@bp.route("/prayer-times")
def prayer_times_page():
    rows = _read_all_rows()
    today_str = datetime.now().strftime("%d/%m/%Y")
    # today onward, capped to a reasonable window
    upcoming = []
    seen_today = False
    for row in rows:
        d = (row.get("Date") or "").strip()
        if d == today_str:
            seen_today = True
        if seen_today:
            upcoming.append(row)
        if len(upcoming) >= 30:
            break
    return render_template("prayer_times.html", rows=upcoming, today_str=today_str,
                             prayers=PRAYER_NAMES, total_rows=len(rows))


# ----------------- Masjid / Speakers -----------------

@bp.route("/masjid-speakers", methods=["GET", "POST"])
def masjid_speakers_page():
    if request.method == "POST":
        cfg = _cfg()
        name = (request.form.get("speaker_name") or "").strip()
        if name:
            cfg["speaker_name"] = name
            _save(cfg)
            flash("Speaker name updated.", "success")
        return redirect(url_for("dashboard.masjid_speakers_page"))

    cfg = _cfg()
    outputs = audio_player.list_outputs(cfg)
    return render_template("masjid_speakers.html", cfg=cfg, outputs=outputs)


# ----------------- Qibla Finder -----------------

@bp.route("/qibla")
def qibla_page():
    cfg = _cfg()
    lat, lon = cfg.get("lat") or 0, cfg.get("lon") or 0
    bearing = islamic_utils.qibla_bearing(lat, lon) if (lat or lon) else None
    return render_template("qibla.html", bearing=bearing, lat=lat, lon=lon)


@bp.route("/qibla/save_location", methods=["POST"])
def qibla_save_location():
    try:
        lat = float(request.form.get("lat"))
        lon = float(request.form.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid coordinates"}), 400
    cfg = _cfg()
    cfg["lat"] = lat
    cfg["lon"] = lon
    _save(cfg)
    return jsonify({"ok": True, "lat": lat, "lon": lon})


@bp.route("/geocode")
def geocode():
    q = request.args.get("q", "")
    results = islamic_utils.geocode_city(q)
    return jsonify({"results": results})


# ----------------- Calendar -----------------

@bp.route("/calendar")
def calendar_page():
    rows = _read_all_rows()
    year_month = request.args.get("month")  # "YYYY-MM"
    now = datetime.now()
    if year_month:
        try:
            y, m = [int(x) for x in year_month.split("-")]
        except ValueError:
            y, m = now.year, now.month
    else:
        y, m = now.year, now.month

    month_rows = []
    for row in rows:
        d = (row.get("Date") or "").strip()
        try:
            dt = datetime.strptime(d, "%d/%m/%Y")
        except ValueError:
            continue
        if dt.year == y and dt.month == m:
            hijri = islamic_utils.gregorian_to_hijri(dt.date())
            month_rows.append({"date": d, "day": dt.day, "row": row,
                                 "hijri": f"{hijri['day']} {hijri['month_name']}"})

    prev_month = (datetime(y, m, 1) - timedelta(days=1))
    next_month = (datetime(y, m, 28) + timedelta(days=7)).replace(day=1)

    return render_template(
        "calendar.html", month_rows=month_rows, prayers=PRAYER_NAMES,
        month_label=datetime(y, m, 1).strftime("%B %Y"),
        prev_month=f"{prev_month.year}-{prev_month.month:02d}",
        next_month=f"{next_month.year}-{next_month.month:02d}",
    )


# ----------------- Logs & History -----------------

@bp.route("/logs")
def logs_page():
    events = history_log.get_recent(200)
    return render_template("logs.html", events=events)


# ----------------- Notifications -----------------

@bp.route("/notifications", methods=["GET", "POST"])
def notifications_page():
    if request.method == "POST":
        cfg = _cfg()
        cfg["notifications_enabled"] = request.form.get("notifications_enabled") == "on"
        try:
            cfg["reminder_minutes_before_azan"] = int(request.form.get("reminder_minutes_before_azan", 10))
        except (TypeError, ValueError):
            pass
        _save(cfg)
        flash("Notification settings saved.", "success")
        return redirect(url_for("dashboard.notifications_page"))

    cfg = _cfg()
    return render_template("notifications.html", cfg=cfg)


@bp.route("/time_sync_status")
def time_sync_status():
    try:
        out = subprocess.run(["timedatectl", "show"], capture_output=True, text=True, timeout=3).stdout
        info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        return jsonify({"synced": info.get("NTPSynchronized") == "yes"})
    except Exception as e:
        return jsonify({"synced": False, "error": str(e)})


# ----------------- Integrations -----------------

@bp.route("/integrations", methods=["GET", "POST"])
def integrations_page():
    if request.method == "POST":
        cfg = _cfg()
        cfg["snapcast_enabled"] = request.form.get("snapcast_enabled") == "on"
        cfg["snapcast_fifo"] = (request.form.get("snapcast_fifo") or "").strip()
        cfg["snapcast_jsonrpc"] = (request.form.get("snapcast_jsonrpc") or "").strip()
        try:
            cfg["snapcast_duck_to"] = int(request.form.get("snapcast_duck_to", 35))
            cfg["snapcast_restore_to"] = int(request.form.get("snapcast_restore_to", 80))
        except (TypeError, ValueError):
            pass
        _save(cfg)
        flash("Integration settings saved.", "success")
        return redirect(url_for("dashboard.integrations_page"))

    cfg = _cfg()
    return render_template("integrations.html", cfg=cfg)
