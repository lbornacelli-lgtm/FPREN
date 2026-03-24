#!/usr/bin/env python3
"""
zone_alert_tts.py
-----------------
Monitors MongoDB for NWS alerts and FL511 traffic incidents, routes them to the
appropriate zone audio folders, and synthesises a spoken WAV for each one.

TTS Engine priority:
  1. ElevenLabs (if ELEVENLABS_API_KEY is set)
  2. Piper (fallback)

Zone routing:
  all_florida   — catch_all=True  → every alert and incident
  north_florida — matches alerts whose area_desc contains a North FL county

Audio output paths:
  zones/{zone}/{alert_folder}/{safe_id}.wav      (NWS alerts)
  zones/{zone}/traffic/{safe_id}.wav             (traffic incidents)

MongoDB collections:
  zone_definitions  — zone → county list
  zone_alert_wavs   — dedup / change-tracking for generated WAVs
  nws_alerts        — source NWS alert documents
  fl_traffic        — source FL511 traffic incident documents
"""

import logging
import os
import re
import shutil
import tempfile
import time
import wave
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MONGO_URI  = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME    = "weather_rss"

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel

VOICE_MODEL = os.getenv(
    "PIPER_VOICE_MODEL",
    "/home/ufuser/Fpren-main/weather_station/voices/en_US-amy-medium.onnx",
)

ZONES_ROOT = "/home/ufuser/Fpren-main/weather_station/audio/zones"
INTERVAL   = 60   # seconds between polls
MAX_WAV_AGE_DAYS = 3

# NWS event type → alert subfolder
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

PRIORITY_1_SEVERITIES       = {"extreme", "severe"}
TRAFFIC_PRIORITY_SEVERITIES = {"major"}
PRIORITY_1_EVENTS           = {"tornado emergency", "flash flood emergency"}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [zone_alert_tts] %(levelname)s: %(message)s",
)
logger = logging.getLogger("zone_alert_tts")


# ---------------------------------------------------------------------------
# TTS Engine
# ---------------------------------------------------------------------------

def _tts_engine_name() -> str:
    return "ElevenLabs" if ELEVENLABS_API_KEY else "Piper"


def _synthesise_elevenlabs(text: str, path: str):
    """Synthesise using ElevenLabs API and save as WAV."""
    from elevenlabs.client import ElevenLabs
    from elevenlabs import VoiceSettings
    from pydub import AudioSegment

    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    audio = client.text_to_speech.convert(
        voice_id=ELEVENLABS_VOICE_ID,
        text=text,
        model_id="eleven_turbo_v2",
        voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75),
    )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_mp3 = path + ".tmp.mp3"
    tmp_wav = path + ".tmp.wav"

    try:
        with open(tmp_mp3, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        AudioSegment.from_mp3(tmp_mp3).export(tmp_wav, format="wav")
        os.replace(tmp_wav, path)
    finally:
        for f in [tmp_mp3, tmp_wav]:
            if os.path.exists(f):
                os.remove(f)


def _synthesise_piper(text: str, path: str, voice):
    """Synthesise using Piper TTS and save as WAV."""
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(voice.config.sample_rate)
        for chunk in voice.synthesize(text):
            wf.writeframes(chunk.audio_int16_bytes)
    os.replace(tmp, path)


def _synthesise(text: str, path: str, voice=None):
    """Synthesise text to WAV using the best available TTS engine."""
    if ELEVENLABS_API_KEY:
        _synthesise_elevenlabs(text, path)
    elif voice:
        _synthesise_piper(text, path, voice)
    else:
        raise RuntimeError("No TTS engine available — set ELEVENLABS_API_KEY or provide Piper voice")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_id(raw: str) -> str:
    return re.sub(r"[^\w\-]", "_", raw)[:120]


def _area_counties(area_desc: str) -> set:
    counties = set()
    for part in area_desc.split(";"):
        part = part.strip().lower()
        part = re.sub(r"\s+county$", "", part)
        counties.add(part)
        counties.update(part.split())
    return counties


def _county_matches_zone(county_name: str, zone_counties: list) -> bool:
    c = county_name.strip().lower()
    for zc in zone_counties:
        if zc in c or c in zc:
            return True
    return False


def _area_matches_zone(area_desc: str, zone_counties: list) -> bool:
    area_set = _area_counties(area_desc)
    for zc in zone_counties:
        for ac in area_set:
            if zc in ac or ac in zc:
                return True
    return False


def _readable_area(area_desc: str) -> str:
    parts = [p.strip() for p in area_desc.split(";") if p.strip()]
    if not parts:
        return area_desc
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th'][min(n % 10, 4)]}"


def _format_traffic_time(last_updated: str) -> str:
    try:
        dt = datetime.strptime(last_updated.strip(), "%m/%d/%y, %I:%M %p")
    except (ValueError, AttributeError):
        return ""
    month = dt.strftime("%B")
    day   = _ordinal(dt.day)
    time_ = dt.strftime("%I:%M %p").lstrip("0")
    return f"{month} {day} at {time_}"


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------

def _build_nws_text(doc: dict) -> str:
    event    = doc.get("event", "Weather Alert")
    severity = doc.get("severity", "")
    area     = _readable_area(doc.get("area_desc", ""))
    headline = (doc.get("headline") or "").strip().rstrip(".")
    is_p1    = event.lower() in PRIORITY_1_EVENTS or severity.lower() in PRIORITY_1_SEVERITIES

    parts = []
    if is_p1:
        parts.append("This is a priority alert.")
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
    desc      = (doc.get("description") or "").strip()

    opener = "This is a priority traffic alert." if severity in TRAFFIC_PRIORITY_SEVERITIES else "Traffic Alert."
    parts  = [opener]

    if inc_type:
        parts.append(inc_type + ".")
    if road:
        parts.append(f"on {road} {direction}".strip() + ("." if county else ""))
    if county:
        parts.append(f"in {county} County.")
    if full_cls:
        parts.append("Road is fully closed.")
    elif lane_desc:
        parts.append(lane_desc + ".")
    if desc and desc.lower() not in (inc_type or "").lower():
        parts.append(desc + ".")

    ts = _format_traffic_time(doc.get("last_updated", ""))
    if ts:
        parts.append(f"Reported {ts}.")

    parts.append("Use caution and drive safely.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Zone routing
# ---------------------------------------------------------------------------

def _load_zones(db) -> list:
    return list(db["zone_definitions"].find({}))


def _zones_for_alert(alert: dict, zones: list) -> list:
    area_desc = alert.get("area_desc", "")
    return [
        z["zone_id"] for z in zones
        if z.get("catch_all") or _area_matches_zone(area_desc, z.get("counties", []))
    ]


def _zones_for_traffic(incident: dict, zones: list) -> list:
    county = (incident.get("county") or "").strip().lower()
    return [
        z["zone_id"] for z in zones
        if z.get("catch_all") or _county_matches_zone(county, z.get("counties", []))
    ]


def _get_alert_folder(event: str, severity: str = "") -> str:
    if severity.lower() in PRIORITY_1_SEVERITIES:
        return "priority_1"
    return ALERT_FOLDER_MAP.get(event.lower().strip(), "other_alerts")


def _copy_to_priority1(src_wav: str, zone_id: str, fname: str):
    for zone in {zone_id, "all_florida"}:
        dest_dir = os.path.join(ZONES_ROOT, zone, "priority_1")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, fname)
        try:
            shutil.copy2(src_wav, dest)
            logger.info("Priority-1 copy [%s/priority_1] %s", zone, fname)
        except Exception as exc:
            logger.error("Failed priority-1 copy to %s: %s", dest, exc)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def process_nws_alerts(db, zones: list, voice=None):
    wavs_col   = db["zone_alert_wavs"]
    alerts_col = db["nws_alerts"]
    cutoff     = datetime.now(timezone.utc) - timedelta(days=MAX_WAV_AGE_DAYS)
    current_ids = set(str(a["alert_id"]) for a in alerts_col.find({}, {"alert_id": 1}))

    # Clean up expired WAVs
    for doc in wavs_col.find({"source_type": "nws_alert", "$or": [
        {"source_id": {"$nin": list(current_ids)}},
        {"generated_at": {"$lt": cutoff}},
    ]}):
        wav = doc.get("wav_path", "")
        if wav and os.path.exists(wav):
            os.remove(wav)
        wavs_col.delete_one({"_id": doc["_id"]})

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
            existing = wavs_col.find_one({
                "source_type": "nws_alert",
                "source_id":   alert_id,
                "zone":        zone_id,
            })
            if (existing
                    and existing.get("fetched_at") == fetched_at
                    and existing.get("alert_folder") == folder
                    and existing.get("wav_path")
                    and os.path.exists(existing["wav_path"])):
                continue

            # Remove old WAV if folder changed (severity upgrade)
            if existing and existing.get("alert_folder") != folder:
                old_wav = existing.get("wav_path", "")
                if old_wav and os.path.exists(old_wav):
                    try:
                        os.remove(old_wav)
                    except OSError:
                        pass

            wav_path = os.path.join(ZONES_ROOT, zone_id, folder, fname)
            try:
                _synthesise(text, wav_path, voice)
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
                        "tts_engine":   _tts_engine_name(),
                    }},
                    upsert=True,
                )
                logger.info("NWS WAV [%s/%s] %s (engine=%s)", zone_id, folder, fname, _tts_engine_name())
            except Exception as exc:
                logger.error("Failed NWS WAV %s/%s/%s: %s", zone_id, folder, fname, exc)


def _parse_last_updated(s: str):
    try:
        return datetime.strptime(s.strip(), "%m/%d/%y, %I:%M %p").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def process_traffic(db, zones: list, voice=None):
    wavs_col    = db["zone_alert_wavs"]
    traffic_col = db["fl_traffic"]
    cutoff      = datetime.now(timezone.utc) - timedelta(days=MAX_WAV_AGE_DAYS)
    current_ids = set(str(t["incident_id"]) for t in traffic_col.find({}, {"incident_id": 1}))

    # Clean up expired WAVs
    for doc in wavs_col.find({"source_type": "traffic", "$or": [
        {"source_id": {"$nin": list(current_ids)}},
        {"generated_at": {"$lt": cutoff}},
    ]}):
        wav = doc.get("wav_path", "")
        if wav and os.path.exists(wav):
            os.remove(wav)
        wavs_col.delete_one({"_id": doc["_id"]})

    for inc in traffic_col.find({}):
        inc_id       = str(inc.get("incident_id", ""))
        last_updated = str(inc.get("last_updated", ""))
        if not inc_id:
            continue

        ts = _parse_last_updated(last_updated)
        if ts and ts < cutoff:
            continue

        target_zones = _zones_for_traffic(inc, zones)
        if not target_zones:
            continue

        text     = _build_traffic_text(inc)
        fname    = _safe_id(inc_id) + ".wav"
        severity = (inc.get("severity") or "").strip()

        for zone_id in target_zones:
            existing = wavs_col.find_one({
                "source_type": "traffic",
                "source_id":   inc_id,
                "zone":        zone_id,
            })
            if (existing
                    and existing.get("last_updated") == last_updated
                    and existing.get("wav_path")
                    and os.path.exists(existing["wav_path"])):
                continue

            wav_path = os.path.join(ZONES_ROOT, zone_id, "traffic", fname)
            try:
                _synthesise(text, wav_path, voice)
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
                        "tts_engine":   _tts_engine_name(),
                    }},
                    upsert=True,
                )
                logger.info("Traffic WAV [%s/traffic] %s (engine=%s)", zone_id, fname, _tts_engine_name())
                if severity.lower() in TRAFFIC_PRIORITY_SEVERITIES:
                    _copy_to_priority1(wav_path, zone_id, fname)
            except Exception as exc:
                logger.error("Failed traffic WAV %s/traffic/%s: %s", zone_id, fname, exc)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    logger.info("TTS engine: %s", _tts_engine_name())

    # Load Piper as fallback if ElevenLabs not configured
    voice = None
    if not ELEVENLABS_API_KEY:
        try:
            from piper import PiperVoice
            logger.info("Loading Piper voice model: %s", VOICE_MODEL)
            voice = PiperVoice.load(VOICE_MODEL)
            logger.info("Piper voice loaded")
        except Exception as e:
            logger.error("Failed to load Piper voice: %s — no TTS available", e)
            return
    else:
        logger.info("ElevenLabs API key found — using ElevenLabs TTS (voice: %s)", ELEVENLABS_VOICE_ID)

    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]

    db["zone_alert_wavs"].create_index(
        [("source_type", 1), ("source_id", 1), ("zone", 1)], unique=True
    )

    logger.info("Connected to MongoDB — %s", DB_NAME)
    logger.info("Output root: %s  |  Interval: %ds", ZONES_ROOT, INTERVAL)

    while True:
        try:
            zones = _load_zones(db)
            logger.info("Loaded %d zone definitions — processing NWS alerts and FL511 traffic", len(zones))
            process_nws_alerts(db, zones, voice)
            process_traffic(db, zones, voice)
        except Exception as exc:
            logger.error("Unexpected error in main loop: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
