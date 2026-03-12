#!/usr/bin/env python3
"""
zone_alert_tts.py
-----------------
Monitors MongoDB for NWS alerts and FL traffic incidents, routes them to the
appropriate zone audio folders based on county membership defined in the
zone_definitions collection, and synthesises a spoken WAV for each one.

Zone routing:
  all_florida   — catch_all=True  → every alert and incident
  north_florida — matches alerts whose area_desc contains a North FL county
                  (and traffic incidents whose county field matches)

Audio output paths (mirror the zone folder structure):
  zones/{zone}/{alert_folder}/{safe_id}.wav      (NWS alerts)
  zones/{zone}/traffic/{safe_id}.wav             (traffic incidents)

MongoDB collections:
  zone_definitions  — zone → county list (seeded externally)
  zone_alert_wavs   — dedup / change-tracking for generated WAVs
  nws_alerts        — source NWS alert documents
  fl_traffic        — source FL511 traffic incident documents

Run directly:
    cd /home/lh_admin/weather_station
    source venv/bin/activate
    python services/zone_alert_tts.py

Or via systemd user service: zone-alert-tts.service
"""

import logging
import os
import re
import shutil
import time
import wave
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne
from piper import PiperVoice

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MONGO_URI  = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME    = "weather_rss"

VOICE_MODEL = os.getenv(
    "PIPER_VOICE_MODEL",
    "/home/lh_admin/weather_station/voices/en_US-amy-medium.onnx",
)

ZONES_ROOT = "/home/lh_admin/weather_station/audio/zones"
INTERVAL   = 60   # seconds between polls

# NWS event type → alert subfolder (mirrors file_router.ALERT_FOLDER_MAP)
ALERT_FOLDER_MAP = {
    "tornado emergency":            "priority_1",
    "tornado warning":              "tornado",
    "tornado watch":                "tornado",
    "severe thunderstorm warning":  "thunderstorm",
    "severe thunderstorm watch":    "thunderstorm",
    "flash flood emergency":        "priority_1",
    "flash flood warning":          "flooding",
    "flash flood watch":            "flooding",
    "flood warning":                "flooding",
    "flood watch":                  "flooding",
    "flood advisory":               "flooding",
    "coastal flood warning":        "flooding",
    "coastal flood watch":          "flooding",
    "coastal flood advisory":       "flooding",
    "hurricane warning":            "hurricane",
    "hurricane watch":              "hurricane",
    "tropical storm warning":       "hurricane",
    "tropical storm watch":         "hurricane",
    "storm surge warning":          "hurricane",
    "storm surge watch":            "hurricane",
    "hurricane local statement":    "hurricane",
    "extreme wind warning":         "hurricane",
    "hurricane force wind warning": "hurricane",
    "hurricane force wind watch":   "hurricane",
    "dense fog advisory":           "fog",
    "freezing fog advisory":        "fog",
    "dense smoke advisory":         "fog",
    "red flag warning":             "fire",
    "fire weather watch":           "fire",
    "extreme fire danger":          "fire",
    "freeze warning":               "freeze",
    "freeze watch":                 "freeze",
    "frost advisory":               "freeze",
    "hard freeze warning":          "freeze",
    "winter storm warning":         "freeze",
    "winter storm watch":           "freeze",
    "winter weather advisory":      "freeze",
    "ice storm warning":            "freeze",
    "blizzard warning":             "freeze",
    "cold weather advisory":        "freeze",
}

# NWS severity levels that always escalate to priority_1 regardless of event type
PRIORITY_1_SEVERITIES = {"extreme", "severe"}

PRIORITY_1_EVENTS = {"tornado emergency", "flash flood emergency"}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [zone_alert_tts] %(levelname)s: %(message)s",
)
logger = logging.getLogger("zone_alert_tts")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_id(raw: str) -> str:
    """Convert an arbitrary string to a filesystem-safe filename stem."""
    return re.sub(r"[^\w\-]", "_", raw)[:120]


def _synthesise(voice: PiperVoice, text: str, path: str):
    """Write Piper TTS output to *path* atomically."""
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(voice.config.sample_rate)
        for chunk in voice.synthesize(text):
            wf.writeframes(chunk.audio_int16_bytes)
    os.replace(tmp, path)


def _area_counties(area_desc: str) -> set:
    """
    Parse 'Baker; Union; Eastern Clay; Western Alachua County' etc. into a
    set of lowercase county tokens.  We keep every word so 'eastern clay'
    and 'clay' both appear, making substring matching straightforward.
    """
    counties = set()
    for part in area_desc.split(";"):
        part = part.strip().lower()
        # strip trailing " county"
        part = re.sub(r"\s+county$", "", part)
        counties.add(part)
        # also add individual words so "eastern clay" also contributes "clay"
        counties.update(part.split())
    return counties


def _county_matches_zone(county_name: str, zone_counties: list) -> bool:
    """Return True if *county_name* (lowercased) is in the zone's county list."""
    c = county_name.strip().lower()
    # exact match or the county name contains a zone county as a token
    for zc in zone_counties:
        if zc in c or c in zc:
            return True
    return False


def _area_matches_zone(area_desc: str, zone_counties: list) -> bool:
    """Return True if any county in area_desc is in the zone's county list."""
    area_set = _area_counties(area_desc)
    for zc in zone_counties:
        for ac in area_set:
            if zc in ac or ac in zc:
                return True
    return False


def _readable_area(area_desc: str) -> str:
    """
    Convert 'Baker; Union; Eastern Clay' → 'Baker, Union, and Eastern Clay'
    for natural speech.
    """
    parts = [p.strip() for p in area_desc.split(";") if p.strip()]
    if not parts:
        return area_desc
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------

def _build_nws_text(doc: dict) -> str:
    event    = doc.get("event", "Weather Alert")
    severity = doc.get("severity", "")
    area     = _readable_area(doc.get("area_desc", ""))
    headline = (doc.get("headline") or "").strip().rstrip(".")
    is_p1_event = event.lower() in PRIORITY_1_EVENTS
    is_severe   = severity.lower() in PRIORITY_1_SEVERITIES

    if is_p1_event or is_severe:
        prefix = "This is a priority alert."
    else:
        prefix = ""

    parts = []
    if prefix:
        parts.append(prefix)
    parts.append(f"A {event} has been issued for {area}." if area else f"A {event} has been issued.")
    if headline:
        parts.append(headline + ".")

    return " ".join(parts)


def _build_traffic_text(doc: dict) -> str:
    county    = (doc.get("county") or "").strip()
    inc_type  = (doc.get("type") or "").strip()
    road      = (doc.get("road") or "").strip()
    direction = (doc.get("direction") or "").strip()
    lane_desc = (doc.get("lane_description") or "").strip()
    full_cls  = doc.get("is_full_closure", False)
    severity  = (doc.get("severity") or "").strip().lower()

    opener = "This is a priority alert." if severity in PRIORITY_1_SEVERITIES else "Traffic Alert."
    parts = [opener]

    if inc_type:
        parts.append(inc_type)
    if road:
        if direction:
            parts.append(f"on {road} {direction}")
        else:
            parts.append(f"on {road}")
    if county:
        parts.append(f"in {county} County.")
    elif parts[-1][-1] != ".":
        parts[-1] += "."

    if full_cls:
        parts.append("Road is fully closed.")
    elif lane_desc:
        parts.append(lane_desc + ".")

    parts.append("Drive safely.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def _load_zones(db) -> list:
    """Return list of zone dicts from zone_definitions collection."""
    return list(db["zone_definitions"].find({}))


def _zones_for_alert(alert: dict, zones: list) -> list:
    """Return zone_ids that this NWS alert should be broadcast to."""
    area_desc = alert.get("area_desc", "")
    matched = []
    for zone in zones:
        if zone.get("catch_all"):
            matched.append(zone["zone_id"])
        elif _area_matches_zone(area_desc, zone.get("counties", [])):
            matched.append(zone["zone_id"])
    return matched


def _zones_for_traffic(incident: dict, zones: list) -> list:
    """Return zone_ids that this traffic incident should be broadcast to."""
    county = (incident.get("county") or "").strip().lower()
    matched = []
    for zone in zones:
        if zone.get("catch_all"):
            matched.append(zone["zone_id"])
        elif _county_matches_zone(county, zone.get("counties", [])):
            matched.append(zone["zone_id"])
    return matched


def _copy_to_priority1(src_wav: str, zone_id: str, fname: str):
    """Copy *src_wav* into zones/{zone_id}/priority_1/ and all_florida/priority_1/."""
    targets = {zone_id, "all_florida"}
    for zone in targets:
        dest_dir = os.path.join(ZONES_ROOT, zone, "priority_1")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, fname)
        try:
            shutil.copy2(src_wav, dest)
            logger.info("Priority-1 copy [%s/priority_1] %s", zone, fname)
        except Exception as exc:
            logger.error("Failed priority-1 copy to %s: %s", dest, exc)


def _get_alert_folder(event: str, severity: str = "") -> str:
    """Return the zone subfolder for an alert.

    Severe and Extreme alerts always go to priority_1 regardless of event type.
    All other alerts are routed by event type keyword.
    """
    if severity.lower() in PRIORITY_1_SEVERITIES:
        return "priority_1"
    return ALERT_FOLDER_MAP.get(event.lower().strip(), "other_alerts")


def process_nws_alerts(voice: PiperVoice, db, zones: list):
    wavs_col   = db["zone_alert_wavs"]
    alerts_col = db["nws_alerts"]

    # Current alert IDs in MongoDB
    current_ids = set(str(a["alert_id"]) for a in alerts_col.find({}, {"alert_id": 1}))

    # Remove WAVs for expired alerts
    expired = wavs_col.find({
        "source_type": "nws_alert",
        "source_id":   {"$nin": list(current_ids)},
    })
    for doc in expired:
        wav = doc.get("wav_path", "")
        if wav and os.path.exists(wav):
            os.remove(wav)
            logger.info("Removed expired alert WAV: %s", wav)
        wavs_col.delete_one({"_id": doc["_id"]})

    # Process active alerts
    for alert in alerts_col.find({}):
        alert_id   = str(alert.get("alert_id", ""))
        fetched_at = str(alert.get("fetched_at", ""))
        if not alert_id:
            continue

        target_zones = _zones_for_alert(alert, zones)
        if not target_zones:
            continue

        event    = alert.get("event", "Weather Alert")
        severity = alert.get("severity", "")
        folder   = _get_alert_folder(event, severity)
        text     = _build_nws_text(alert)
        fname    = _safe_id(alert_id) + ".wav"

        for zone_id in target_zones:
            # Check if already generated with same content in the correct folder
            existing = wavs_col.find_one({
                "source_type": "nws_alert",
                "source_id":   alert_id,
                "zone":        zone_id,
            })
            if (existing
                    and existing.get("fetched_at") == fetched_at
                    and existing.get("alert_folder") == folder):
                continue  # already current and in the right folder

            # If the alert was previously in a different folder (e.g. severity upgrade),
            # remove the old WAV file before writing the new one.
            if existing and existing.get("alert_folder") != folder:
                old_wav = existing.get("wav_path", "")
                if old_wav and os.path.exists(old_wav):
                    try:
                        os.remove(old_wav)
                        logger.info("Removed old-folder WAV (severity re-route): %s", old_wav)
                    except OSError:
                        pass

            wav_path = os.path.join(ZONES_ROOT, zone_id, folder, fname)
            try:
                _synthesise(voice, text, wav_path)
                wavs_col.update_one(
                    {"source_type": "nws_alert", "source_id": alert_id, "zone": zone_id},
                    {"$set": {
                        "source_type":  "nws_alert",
                        "source_id":    alert_id,
                        "zone":         zone_id,
                        "alert_folder": folder,
                        "wav_path":     wav_path,
                        "event":        event,
                        "severity":     severity,
                        "area_desc":    alert.get("area_desc", ""),
                        "fetched_at":   fetched_at,
                        "generated_at": datetime.now(timezone.utc),
                    }},
                    upsert=True,
                )
                logger.info("NWS WAV [%s/%s] %s  (severity=%s)", zone_id, folder, fname, severity or "—")
            except Exception as exc:
                logger.error("Failed NWS WAV %s/%s/%s: %s", zone_id, folder, fname, exc)


def process_traffic(voice: PiperVoice, db, zones: list):
    wavs_col    = db["zone_alert_wavs"]
    traffic_col = db["fl_traffic"]

    # Current incident IDs
    current_ids = set(str(t["incident_id"]) for t in traffic_col.find({}, {"incident_id": 1}))

    # Remove WAVs for resolved incidents
    expired = wavs_col.find({
        "source_type": "traffic",
        "source_id":   {"$nin": list(current_ids)},
    })
    for doc in expired:
        wav = doc.get("wav_path", "")
        if wav and os.path.exists(wav):
            os.remove(wav)
            logger.info("Removed expired traffic WAV: %s", wav)
        wavs_col.delete_one({"_id": doc["_id"]})

    # Process active incidents
    for inc in traffic_col.find({}):
        inc_id       = str(inc.get("incident_id", ""))
        last_updated = str(inc.get("last_updated", ""))
        if not inc_id:
            continue

        target_zones = _zones_for_traffic(inc, zones)
        if not target_zones:
            continue

        text  = _build_traffic_text(inc)
        fname = _safe_id(inc_id) + ".wav"

        for zone_id in target_zones:
            existing = wavs_col.find_one({
                "source_type": "traffic",
                "source_id":   inc_id,
                "zone":        zone_id,
            })
            if existing and existing.get("last_updated") == last_updated:
                continue

            wav_path = os.path.join(ZONES_ROOT, zone_id, "traffic", fname)
            severity = (inc.get("severity") or "").strip()
            try:
                _synthesise(voice, text, wav_path)
                wavs_col.update_one(
                    {"source_type": "traffic", "source_id": inc_id, "zone": zone_id},
                    {"$set": {
                        "source_type":  "traffic",
                        "source_id":    inc_id,
                        "zone":         zone_id,
                        "alert_folder": "traffic",
                        "wav_path":     wav_path,
                        "county":       inc.get("county", ""),
                        "road":         inc.get("road", ""),
                        "severity":     severity,
                        "last_updated": last_updated,
                        "generated_at": datetime.now(timezone.utc),
                    }},
                    upsert=True,
                )
                logger.info("Traffic WAV [%s/traffic] %s", zone_id, fname)
                if severity.lower() in PRIORITY_1_SEVERITIES:
                    _copy_to_priority1(wav_path, zone_id, fname)
            except Exception as exc:
                logger.error("Failed traffic WAV %s/traffic/%s: %s", zone_id, fname, exc)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    logger.info("Loading Piper voice model: %s", VOICE_MODEL)
    voice = PiperVoice.load(VOICE_MODEL)

    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]

    # Ensure indexes for fast dedup lookups
    db["zone_alert_wavs"].create_index(
        [("source_type", 1), ("source_id", 1), ("zone", 1)], unique=True
    )

    logger.info("Connected to MongoDB — %s", DB_NAME)
    logger.info("Output root: %s  |  Interval: %ds", ZONES_ROOT, INTERVAL)

    while True:
        try:
            zones = _load_zones(db)
            logger.info("Loaded %d zone definitions", len(zones))
            process_nws_alerts(voice, db, zones)
            process_traffic(voice, db, zones)
        except Exception as exc:
            logger.error("Unexpected error in main loop: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
