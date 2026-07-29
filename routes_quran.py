#!/usr/bin/env python3
import os

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import quran_player

bp = Blueprint("quran", __name__)

_config_lock = None
_load_config = None
_AUDIO_FOLDER = None


def init(app, config_lock, load_config, save_config, audio_folder="audio"):
    global _config_lock, _load_config, _AUDIO_FOLDER
    _config_lock = config_lock
    _load_config = load_config
    _AUDIO_FOLDER = audio_folder
    if "quran" not in app.blueprints:
        app.register_blueprint(bp)


def _cfg():
    with _config_lock:
        return _load_config()


@bp.route("/quran")
def quran_page():
    tracks = [t for t in quran_player.list_tracks() if t["type"] != "live"]
    live_tracks = [t for t in quran_player.list_tracks() if t["type"] == "live"]
    return render_template("quran.html", tracks=tracks, live_tracks=live_tracks,
                             status=quran_player.get_status())


@bp.route("/quran/upload", methods=["POST"])
def quran_upload():
    f = request.files.get("file")
    title = (request.form.get("title") or "").strip()
    if not f or f.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("quran.quran_page"))
    filename = f.filename
    try:
        os.makedirs(_AUDIO_FOLDER, exist_ok=True)
        f.save(os.path.join(_AUDIO_FOLDER, filename))
        quran_player.add_file_track(title or filename, filename)
        flash(f"Added '{title or filename}'.", "success")
    except Exception as e:
        flash(f"Upload failed: {e}", "danger")
    return redirect(url_for("quran.quran_page"))


@bp.route("/quran/add_url", methods=["POST"])
def quran_add_url():
    title = (request.form.get("title") or "").strip()
    url = (request.form.get("url") or "").strip()
    is_live = request.form.get("live") == "on"
    if not url:
        flash("No URL provided.", "danger")
        return redirect(url_for("quran.quran_page"))
    quran_player.add_url_track(title or url, url, live=is_live)
    flash(f"Added {'live stream' if is_live else 'track'} '{title or url}'.", "success")
    return redirect(url_for("quran.quran_page"))


@bp.route("/quran/remove/<track_id>", methods=["POST"])
def quran_remove(track_id):
    quran_player.remove_track(track_id)
    flash("Removed.", "success")
    return redirect(url_for("quran.quran_page"))


@bp.route("/quran/play/<track_id>", methods=["POST"])
def quran_play(track_id):
    ok, err = quran_player.play_track(_cfg(), track_id, audio_folder=_AUDIO_FOLDER)
    return jsonify({"ok": ok, "error": err})


@bp.route("/quran/pause", methods=["POST"])
def quran_pause():
    ok, err = quran_player.pause(audio_folder=_AUDIO_FOLDER)
    return jsonify({"ok": ok, "error": err})


@bp.route("/quran/resume", methods=["POST"])
def quran_resume():
    ok, err = quran_player.resume()
    return jsonify({"ok": ok, "error": err})


@bp.route("/quran/stop", methods=["POST"])
def quran_stop():
    ok, err = quran_player.stop(audio_folder=_AUDIO_FOLDER)
    return jsonify({"ok": ok, "error": err})


@bp.route("/quran/restart", methods=["POST"])
def quran_restart():
    ok, err = quran_player.restart_from_beginning(audio_folder=_AUDIO_FOLDER)
    return jsonify({"ok": ok, "error": err})


@bp.route("/quran/seek", methods=["POST"])
def quran_seek():
    try:
        pos = float(request.form.get("position", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad position"}), 400
    ok, err = quran_player.seek(pos, audio_folder=_AUDIO_FOLDER)
    return jsonify({"ok": ok, "error": err})


@bp.route("/quran/status")
def quran_status():
    return jsonify(quran_player.get_status())
