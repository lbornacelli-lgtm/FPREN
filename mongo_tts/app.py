import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for
from pymongo import MongoClient

import db
import importer
import tts
from config import FLASK_DEBUG, FLASK_PORT, MONGO_URI, DB_NAME, WAV_OUTPUT_DIR

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit


def _serialize(entry):
    entry["_id"] = str(entry["_id"])
    return entry


@app.route("/")
def index():
    entries = [_serialize(e) for e in db.all_entries()]
    return render_template("index.html", entries=entries, wav_dir=str(WAV_OUTPUT_DIR))


@app.route("/import", methods=["POST"])
def import_file_route():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded"}), 400

    suffix = Path(f.filename).suffix.lower()
    if suffix not in (".json", ".xml"):
        return jsonify({"error": "Only .json and .xml files are supported"}), 400

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = importer.import_file(tmp_path)
    finally:
        os.unlink(tmp_path)

    return redirect(url_for("index") + f"?msg=Imported+{result['imported']}+records,+converted+{result['converted']}")


@app.route("/convert/<entry_id>", methods=["POST"])
def convert_one(entry_id):
    entry = db.get_entry(entry_id)
    if not entry:
        return jsonify({"error": "Entry not found"}), 404
    try:
        wav = tts.convert_entry(entry)
        db.update_wav(entry_id, wav)
        return redirect(url_for("index") + f"?msg=Converted+{entry_id}")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/convert_all", methods=["POST"])
def convert_all():
    entries = db.all_entries()
    converted, failed = 0, 0
    for entry in entries:
        if entry.get("description"):
            try:
                wav = tts.convert_entry(entry)
                db.update_wav(entry["_id"], wav)
                converted += 1
            except Exception as e:
                print(f"TTS failed for {entry['_id']}: {e}")
                failed += 1
    return redirect(url_for("index") + f"?msg=Converted+{converted},+failed+{failed}")


@app.route("/wav/<entry_id>")
def serve_wav(entry_id):
    entry = db.get_entry(entry_id)
    if not entry or not entry.get("_wav_file"):
        return jsonify({"error": "WAV not found"}), 404
    wav_path = Path(entry["_wav_file"])
    if not wav_path.exists():
        return jsonify({"error": "WAV file missing from disk"}), 404
    return send_file(wav_path, mimetype="audio/wav")


@app.route("/delete/<entry_id>", methods=["POST"])
def delete_entry(entry_id):
    db.delete_entry(entry_id)
    return redirect(url_for("index") + "?msg=Deleted")


@app.route("/feedback", methods=["POST"])
def submit_feedback():
    name = request.form.get("name", "").strip()
    message = request.form.get("message", "").strip()
    if not message:
        return redirect(url_for("index") + "?msg=Feedback+message+is+required")
    client = MongoClient(MONGO_URI)
    client[DB_NAME]["feedback"].insert_one({
        "name": name or "Anonymous",
        "message": message,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    })
    return redirect(url_for("index") + "?msg=Thank+you+for+your+feedback!")


if __name__ == "__main__":
    app.run(port=FLASK_PORT, debug=FLASK_DEBUG)
