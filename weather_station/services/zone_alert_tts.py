#!/usr/bin/env python3
# weather_station/services/zone_alert_tts.py
"""
zone_alert_tts.py
-----------------
Monitors MongoDB for NWS alerts, FL511 traffic incidents, and airport METAR
observations, routes them to the appropriate zone audio folders, and synthesises
MP3s via TTSService (Piper).

Zone routing:
  all_florida   — catch_all=True  → every item
  north_florida — matches alerts/airports whose county is in North FL
  (etc. for central, south, tampa, orlando, jacksonville, gainesville, miami)

Audio output paths:
  zones/{zone}/{alert_folder}/{safe_id}.mp3      (NWS alerts)
  zones/{zone}/traffic/{safe_id}.mp3             (traffic incidents)
  zones/{zone}/airport_weather/{icao}.mp3        (airport METAR)

MongoDB collections:
  zone_definitions  — zone → county list
  zone_alert_wavs   — dedup / change-tracking for generated audio
  nws_alerts        — source NWS alert documents
  fl_traffic        — source FL511 traffic incident documents
  airport_metar     — ASOS station observations
"""

import csv
import json
import logging
import os
import re
import shutil
import signal
import time
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient

from weather_station.core.tts_service import TTSService
from weather_station.services import ai_classifier, elevenlabs_tts

# ── Configuration ─────────────────────────────────────────────────────────────

MONGO_URI              = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME                = "weather_rss"
ZONES_ROOT             = os.getenv("ZONES_ROOT", "/home/ufuser/Fpren-main/weather_station/audio/zones")
INTERVAL               = int(os.getenv("ZONE_ALERT_INTERVAL", 60))
MAX_AGE_DAYS           = int(os.getenv("MAX_WAV_AGE_DAYS", 3))
PROGRESS_FILE          = "/tmp/fpren_transcode_progress.json"
TRAFFIC_MAX_AGE_HOURS  = int(os.getenv("TRAFFIC_MAX_AGE_HOURS", "12"))
TRAFFIC_SEVERITIES     = {s.lower() for s in os.getenv("TRAFFIC_SEVERITIES", "major,intermediate").split(",")}
TRAFFIC_LOG_FILE       = os.getenv("TRAFFIC_LOG_FILE",
                             "/home/ufuser/Fpren-main/reports/traffic_log.csv")

# ── Alert folder map ──────────────────────────────────────────────────────────

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
PRIORITY_1_EVENTS           = {"tornado emergency", "flash flood emergency"}
TRAFFIC_PRIORITY_SEVERITIES = {"major"}

# ── Airport → county mapping (FL airports only; non-FL omitted → skipped) ─────

AIRPORT_COUNTY_MAP = {
    "KGNV": "alachua",       # Gainesville Regional
    "KOCF": "marion",        # Ocala Regional
    "KJAX": "duval",         # Jacksonville International
    "KTLH": "leon",          # Tallahassee International
    "KPNS": "escambia",      # Pensacola International
    "KMCO": "orange",        # Orlando International
    "KDAB": "volusia",       # Daytona Beach International
    "KTPA": "hillsborough",  # Tampa International
    "KLAL": "polk",          # Lakeland Linder International
    "KSRQ": "manatee",       # Sarasota-Bradenton International
    "KRSW": "lee",           # Ft Myers SW Florida International
    "KFLL": "broward",       # Ft Lauderdale-Hollywood International
    "KMIA": "miami-dade",    # Miami International
    "KAPF": "collier",       # Naples Municipal
    "KEYW": "monroe",        # Key West International
    "KPBI": "palm beach",    # Palm Beach International
    "KECP": "bay",           # Northwest Florida Beaches International (Panama City)
    "KPAK": "st. johns",     # Palatka Municipal
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [zone_alert_tts] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("zone_alert_tts")

# ── Helpers ───────────────────────────────────────────────────────────────────

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
    return any(zc in c or c in zc for zc in zone_counties)


def _area_matches_zone(area_desc: str, zone_counties: list) -> bool:
    area_set = _area_counties(area_desc)
    return any(zc in ac or ac in zc for zc in zone_counties for ac in area_set)


def _readable_area(area_desc: str) -> str:
    parts = [p.strip() for p in area_desc.split(";") if p.strip()]
    if not parts:        return area_desc
    if len(parts) == 1:  return parts[0]
    if len(parts) == 2:  return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th'][min(n % 10, 4)]}"


def _format_traffic_time(last_updated: str) -> str:
    try:
        dt = datetime.strptime(last_updated.strip(), "%m/%d/%y, %I:%M %p")
        return f"{dt.strftime('%B')} {_ordinal(dt.day)} at {dt.strftime('%I:%M %p').lstrip('0')}"
    except (ValueError, AttributeError):
        return ""


def _parse_last_updated(s: str):
    try:
        return datetime.strptime(s.strip(), "%m/%d/%y, %I:%M %p").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _get_alert_folder(event: str, severity: str = "") -> str:
    if severity.lower() in PRIORITY_1_SEVERITIES:
        return "priority_1"
    return ALERT_FOLDER_MAP.get(event.lower().strip(), "other_alerts")


def _mp3_path(base_path: str) -> str:
    """Ensure path uses .mp3 extension."""
    return os.path.splitext(base_path)[0] + ".mp3"


def _write_progress(state: dict):
    """Atomically write transcode progress to PROGRESS_FILE."""
    tmp = PROGRESS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, PROGRESS_FILE)
    except Exception as exc:
        logger.warning("Could not write progress file: %s", exc)


def _zone_progress(progress: dict, zone_id: str) -> dict:
    """Return (and initialise if missing) the per-zone progress sub-dict."""
    return progress["zones"].setdefault(
        zone_id,
        {"nws":     {"done": 0, "skipped": 0, "failed": 0},
         "traffic": {"done": 0, "skipped": 0, "failed": 0}},
    )

# ── Airport helpers ───────────────────────────────────────────────────────────

_WIND_DIRS = [
    "north", "north-northeast", "northeast", "east-northeast",
    "east", "east-southeast", "southeast", "south-southeast",
    "south", "south-southwest", "southwest", "west-southwest",
    "west", "west-northwest", "northwest", "north-northwest",
]

def _wind_direction(degrees) -> str:
    try:
        idx = round(int(degrees) / 22.5) % 16
        return _WIND_DIRS[idx]
    except (TypeError, ValueError):
        return "variable"


def _celsius_to_f(c) -> int:
    try:
        return round(float(c) * 9 / 5 + 32)
    except (TypeError, ValueError):
        return None


_COVER_WORDS = {
    "SKC": "skies clear", "CLR": "skies clear",
    "FEW": "few clouds",  "SCT": "scattered clouds",
    "BKN": "broken clouds", "OVC": "overcast",
    "VV":  "vertical visibility",
}

_FLTCAT_WORDS = {
    "VFR":  "VFR",
    "MVFR": "marginal VFR",
    "IFR":  "IFR",
    "LIFR": "low IFR",
}

def _sky_text(clouds: list) -> str:
    if not clouds:
        return "skies clear"
    lowest = min(clouds, key=lambda c: c.get("base") or 99999)
    cover  = _COVER_WORDS.get(lowest.get("cover", ""), "clouds")
    base   = lowest.get("base")
    if base:
        return f"{cover} at {base:,} feet"
    return cover


def _parse_gust(raw_ob: str) -> int | None:
    """Extract gust speed (knots) from raw METAR string, e.g. '06013G25KT'."""
    m = re.search(r'\d{3}\d{2}G(\d{2,3})KT', raw_ob or "")
    return int(m.group(1)) if m else None


def _build_airport_text(doc: dict) -> str:
    icao   = doc.get("icaoId", "")
    name   = doc.get("name", "").split(",")[0].strip()  # e.g. "Orlando Intl"
    temp_f = _celsius_to_f(doc.get("temp"))
    dewp_f = _celsius_to_f(doc.get("dewp"))
    wdir   = doc.get("wdir")
    wspd   = doc.get("wspd")
    visib  = doc.get("visib", "")
    clouds = doc.get("clouds", [])
    flt    = doc.get("fltCat", "")
    raw_ob = doc.get("rawOb", "")
    gust   = _parse_gust(raw_ob)

    parts = [f"Airport weather report for {name}."]

    if temp_f is not None:
        parts.append(f"Temperature {temp_f} degrees Fahrenheit.")

    if wdir is not None and wspd is not None:
        wind_dir = _wind_direction(wdir)
        if wspd == 0:
            parts.append("Winds calm.")
        elif gust:
            parts.append(f"Winds from the {wind_dir} at {wspd} knots, gusting to {gust}.")
        else:
            parts.append(f"Winds from the {wind_dir} at {wspd} knots.")

    vis_str = str(visib).replace("+", " or more")
    parts.append(f"Visibility {vis_str} miles.")

    parts.append(_sky_text(clouds).capitalize() + ".")

    if flt:
        parts.append(f"Flight category: {_FLTCAT_WORDS.get(flt, flt)}.")

    return " ".join(parts)

# ── Text builders ─────────────────────────────────────────────────────────────

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

    parts = ["This is a priority traffic alert." if severity in TRAFFIC_PRIORITY_SEVERITIES else "Traffic Alert."]
    if inc_type:   parts.append(inc_type + ".")
    if road:       parts.append(f"on {road} {direction}".strip() + ("." if county else ""))
    if county:     parts.append(f"in {county} County.")
    if full_cls:   parts.append("Road is fully closed.")
    elif lane_desc: parts.append(lane_desc + ".")
    if desc and desc.lower() not in (inc_type or "").lower():
        parts.append(desc + ".")
    ts = _format_traffic_time(doc.get("last_updated", ""))
    if ts:         parts.append(f"Reported {ts}.")
    parts.append("Use caution and drive safely.")
    return " ".join(parts)

# ── Zone routing ──────────────────────────────────────────────────────────────

def _load_zones(db) -> list:
    return list(db["zone_definitions"].find({}))


def _zones_for_alert(alert: dict, zones: list) -> list:
    area_desc = alert.get("area_desc", "")
    event     = alert.get("event", "").lower().strip()
    result    = []
    for z in zones:
        ef = z.get("event_filter")
        if ef and event not in [e.lower() for e in ef]:
            continue
        if z.get("catch_all") or _area_matches_zone(area_desc, z.get("counties", [])):
            result.append(z["zone_id"])
    return result


def _zones_for_airport(icao: str, zones: list) -> list:
    """Return zone_ids that should receive audio for this airport.
    Non-FL airports (not in AIRPORT_COUNTY_MAP) return an empty list.
    """
    county = AIRPORT_COUNTY_MAP.get(icao.upper())
    if not county:
        return []
    return [z["zone_id"] for z in zones
            if z.get("catch_all") or _county_matches_zone(county, z.get("counties", []))]


def _zones_for_traffic(incident: dict, zones: list) -> list:
    county = (incident.get("county") or "").strip().lower()
    return [z["zone_id"] for z in zones
            if z.get("catch_all") or _county_matches_zone(county, z.get("counties", []))]


def _copy_to_priority1(src: str, zone_id: str, fname: str):
    # Only copy into the originating zone's priority_1 folder.
    # all_florida is catch_all and already receives a tracked copy under traffic/,
    # so adding an untracked copy there would accumulate without cleanup.
    dest_dir = os.path.join(ZONES_ROOT, zone_id, "priority_1")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, fname)
    try:
        shutil.copy2(src, dest)
        logger.info("Priority-1 copy → %s/priority_1/%s", zone_id, fname)
    except OSError as e:
        logger.error("Priority-1 copy failed to %s: %s", dest, e)

# ── Processing ────────────────────────────────────────────────────────────────

def process_nws_alerts(db, zones: list, tts: TTSService, progress: dict = None):
    wavs_col    = db["zone_alert_wavs"]
    alerts_col  = db["nws_alerts"]
    cutoff      = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    current_ids = {str(a["alert_id"]) for a in alerts_col.find({}, {"alert_id": 1})}

    for doc in wavs_col.find({"source_type": "nws_alert", "$or": [
        {"source_id": {"$nin": list(current_ids)}},
        {"generated_at": {"$lt": cutoff}},
    ]}):
        path = doc.get("wav_path", "")
        if path and os.path.exists(path):
            os.remove(path)
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
        fname    = _safe_id(alert_id) + ".mp3"

        # ── AI classification (once per alert, before zone loop) ──────────────
        ai_severity    = None
        use_elevenlabs = False
        spoken_text    = _build_nws_text(alert)  # fallback always ready
        try:
            ai_result      = ai_classifier.process_alert(alert)
            spoken_text    = ai_result["ai_text"]
            ai_severity    = ai_result["ai_severity"]
            use_elevenlabs = (ai_result["tts_engine"] == "elevenlabs")
            logger.info(
                "AI classified alert %s → severity=%s engine=%s",
                alert_id, ai_severity, ai_result["tts_engine"],
            )
        except Exception as ai_exc:
            logger.warning(
                "AI classifier failed for alert %s: %s — using Piper fallback",
                alert_id, ai_exc,
            )

        for zone_id in target_zones:
            zp = _zone_progress(progress, zone_id) if progress is not None else None
            existing = wavs_col.find_one({
                "source_type": "nws_alert",
                "source_id":   alert_id,
                "zone":        zone_id,
            })
            audio_path = _mp3_path(os.path.join(ZONES_ROOT, zone_id, folder, fname))

            if (existing
                    and existing.get("fetched_at") == fetched_at
                    and existing.get("alert_folder") == folder
                    and existing.get("wav_path")
                    and os.path.exists(existing["wav_path"])):
                if zp is not None:
                    zp["nws"]["skipped"] += 1
                continue

            if existing and existing.get("alert_folder") != folder:
                old = existing.get("wav_path", "")
                if old and os.path.exists(old):
                    try:
                        os.remove(old)
                    except OSError:
                        pass

            try:
                engine_used = "Piper"
                if use_elevenlabs:
                    result = elevenlabs_tts.say(spoken_text, output_file=audio_path)
                    if result:
                        engine_used = "ElevenLabs"
                    else:
                        logger.warning(
                            "ElevenLabs failed for %s/%s — falling back to Piper",
                            zone_id, fname,
                        )
                        tts.say(spoken_text, output_file=audio_path)
                else:
                    tts.say(spoken_text, output_file=audio_path)

                update_doc = {
                    "source_type":  "nws_alert",
                    "source_id":    alert_id,
                    "zone":         zone_id,
                    "alert_folder": folder,
                    "wav_path":     audio_path,
                    "event":        event,
                    "severity":     severity,
                    "area_desc":    alert.get("area_desc", ""),
                    "fetched_at":   fetched_at,
                    "generated_at": datetime.now(timezone.utc),
                    "tts_engine":   engine_used,
                }
                if ai_severity is not None:
                    update_doc["ai_severity"] = ai_severity

                wavs_col.update_one(
                    {"source_type": "nws_alert", "source_id": alert_id, "zone": zone_id},
                    {"$set": update_doc},
                    upsert=True,
                )
                logger.info("NWS MP3 [%s/%s] %s (engine=%s)", zone_id, folder, fname, engine_used)
                if zp is not None:
                    zp["nws"]["done"] += 1
                    _write_progress(progress)
            except Exception as e:
                logger.error("Failed NWS MP3 %s/%s/%s: %s", zone_id, folder, fname, e)
                if zp is not None:
                    zp["nws"]["failed"] += 1
                    _write_progress(progress)


_TRAFFIC_LOG_FIELDS = [
    "logged_at", "incident_id", "type", "event_subtype", "severity",
    "county", "dot_district", "road", "direction", "location",
    "lane_description", "is_full_closure", "description",
    "start_time", "end_time", "last_updated", "fetched_at",
]

def _append_traffic_log(incidents: list):
    """Append new traffic incidents to the CSV log file."""
    if not incidents:
        return
    os.makedirs(os.path.dirname(os.path.abspath(TRAFFIC_LOG_FILE)), exist_ok=True)
    write_header = not os.path.exists(TRAFFIC_LOG_FILE)
    try:
        with open(TRAFFIC_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_TRAFFIC_LOG_FIELDS,
                                    extrasaction="ignore")
            if write_header:
                writer.writeheader()
            for inc in incidents:
                row = {k: inc.get(k, "") for k in _TRAFFIC_LOG_FIELDS}
                row["logged_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                if hasattr(row.get("fetched_at"), "strftime"):
                    row["fetched_at"] = row["fetched_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
                writer.writerow(row)
        logger.info("Traffic log: appended %d rows → %s", len(incidents), TRAFFIC_LOG_FILE)
    except Exception as exc:
        logger.error("Could not write traffic log: %s", exc)


def process_traffic(db, zones: list, tts: TTSService, progress: dict = None):
    wavs_col    = db["zone_alert_wavs"]
    traffic_col = db["fl_traffic"]
    age_cutoff  = datetime.now(timezone.utc) - timedelta(hours=TRAFFIC_MAX_AGE_HOURS)
    wav_cutoff  = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    current_ids = {str(t["incident_id"]) for t in traffic_col.find({}, {"incident_id": 1})}

    # Clean up audio for incidents that are gone or too old
    for doc in wavs_col.find({"source_type": {"$in": ["traffic", "traffic_p1"]}, "$or": [
        {"source_id": {"$nin": list(current_ids)}},
        {"generated_at": {"$lt": wav_cutoff}},
    ]}):
        path = doc.get("wav_path", "")
        if path and os.path.exists(path):
            os.remove(path)
        wavs_col.delete_one({"_id": doc["_id"]})

    new_for_log = []

    for inc in traffic_col.find({}):
        inc_id       = str(inc.get("incident_id", ""))
        last_updated = str(inc.get("last_updated", ""))
        severity     = (inc.get("severity") or "").strip()
        if not inc_id:
            continue

        # Skip incidents outside our age + severity filter
        ts = _parse_last_updated(last_updated)
        if not ts or ts < age_cutoff:
            continue
        if severity.lower() not in TRAFFIC_SEVERITIES:
            continue

        # Log to CSV if not previously recorded
        if not wavs_col.find_one({"source_type": "traffic", "source_id": inc_id}):
            new_for_log.append(inc)

        target_zones = _zones_for_traffic(inc, zones)
        if not target_zones:
            continue

        text     = _build_traffic_text(inc)
        fname    = _safe_id(inc_id) + ".mp3"
        severity = (inc.get("severity") or "").strip()

        for zone_id in target_zones:
            zp = _zone_progress(progress, zone_id) if progress is not None else None
            existing = wavs_col.find_one({
                "source_type": "traffic",
                "source_id":   inc_id,
                "zone":        zone_id,
            })
            audio_path = _mp3_path(os.path.join(ZONES_ROOT, zone_id, "traffic", fname))

            if (existing
                    and existing.get("last_updated") == last_updated
                    and existing.get("wav_path")
                    and os.path.exists(existing["wav_path"])):
                if zp is not None:
                    zp["traffic"]["skipped"] += 1
                continue

            try:
                tts.say(text, output_file=audio_path)
                wavs_col.update_one(
                    {"source_type": "traffic", "source_id": inc_id, "zone": zone_id},
                    {"$set": {
                        "source_type":  "traffic",
                        "source_id":    inc_id,
                        "zone":         zone_id,
                        "alert_folder": "traffic",
                        "wav_path":     audio_path,
                        "county":       inc.get("county", ""),
                        "road":         inc.get("road", ""),
                        "severity":     severity,
                        "last_updated": last_updated,
                        "generated_at": datetime.now(timezone.utc),
                        "tts_engine":   "Piper",
                    }},
                    upsert=True,
                )
                logger.info("Traffic MP3 [%s/traffic] %s", zone_id, fname)
                if zp is not None:
                    zp["traffic"]["done"] += 1
                    _write_progress(progress)
                if severity.lower() in TRAFFIC_PRIORITY_SEVERITIES:
                    _copy_to_priority1(audio_path, zone_id, fname)
                    p1_path = _mp3_path(os.path.join(ZONES_ROOT, zone_id, "priority_1", fname))
                    wavs_col.update_one(
                        {"source_type": "traffic_p1", "source_id": inc_id, "zone": zone_id},
                        {"$set": {
                            "source_type":  "traffic_p1",
                            "source_id":    inc_id,
                            "zone":         zone_id,
                            "alert_folder": "priority_1",
                            "wav_path":     p1_path,
                            "county":       inc.get("county", ""),
                            "road":         inc.get("road", ""),
                            "severity":     severity,
                            "last_updated": last_updated,
                            "generated_at": datetime.now(timezone.utc),
                            "tts_engine":   "Piper",
                        }},
                        upsert=True,
                    )
                    logger.info("Priority-1 record tracked [%s/priority_1] %s", zone_id, fname)
            except Exception as e:
                logger.error("Failed traffic MP3 %s/traffic/%s: %s", zone_id, fname, e)
                if zp is not None:
                    zp["traffic"]["failed"] += 1
                    _write_progress(progress)

    _append_traffic_log(new_for_log)


def process_airport_weather(db, zones: list, tts: TTSService, progress: dict = None):
    """Transcode current METAR observations to MP3 per zone."""
    wavs_col    = db["zone_alert_wavs"]
    metar_col   = db["airport_metar"]

    for doc in metar_col.find({}):
        icao     = doc.get("icaoId", "").upper()
        obs_time = str(doc.get("obsTime", ""))
        if not icao or not obs_time:
            continue

        target_zones = _zones_for_airport(icao, zones)
        if not target_zones:
            continue  # non-FL airport

        text  = _build_airport_text(doc)
        fname = icao.lower() + ".mp3"

        for zone_id in target_zones:
            zp       = _zone_progress(progress, zone_id) if progress is not None else None
            existing = wavs_col.find_one({
                "source_type": "airport_weather",
                "source_id":   icao,
                "zone":        zone_id,
            })
            audio_path = os.path.join(ZONES_ROOT, zone_id, "airport_weather", fname)

            if (existing
                    and existing.get("obs_time") == obs_time
                    and existing.get("wav_path")
                    and os.path.exists(existing["wav_path"])):
                if zp is not None:
                    zp.setdefault("airport", {"done": 0, "skipped": 0, "failed": 0})
                    zp["airport"]["skipped"] += 1
                continue

            try:
                tts.say(text, output_file=audio_path)
                wavs_col.update_one(
                    {"source_type": "airport_weather", "source_id": icao, "zone": zone_id},
                    {"$set": {
                        "source_type":  "airport_weather",
                        "source_id":    icao,
                        "zone":         zone_id,
                        "alert_folder": "airport_weather",
                        "wav_path":     audio_path,
                        "name":         doc.get("name", ""),
                        "obs_time":     obs_time,
                        "flt_cat":      doc.get("fltCat", ""),
                        "generated_at": datetime.now(timezone.utc),
                        "tts_engine":   "Piper",
                    }},
                    upsert=True,
                )
                logger.info("Airport MP3 [%s/%s] %s", zone_id, "airport_weather", fname)
                if zp is not None:
                    zp.setdefault("airport", {"done": 0, "skipped": 0, "failed": 0})
                    zp["airport"]["done"] += 1
                    _write_progress(progress)
            except Exception as e:
                logger.error("Failed airport MP3 %s/%s: %s", zone_id, fname, e)
                if zp is not None:
                    zp.setdefault("airport", {"done": 0, "skipped": 0, "failed": 0})
                    zp["airport"]["failed"] += 1
                    _write_progress(progress)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    tts     = TTSService()
    client  = MongoClient(MONGO_URI)
    db      = client[DB_NAME]
    running = True

    def _shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received — stopping.")
        running = False

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    db["zone_alert_wavs"].create_index(
        [("source_type", 1), ("source_id", 1), ("zone", 1)], unique=True
    )

    logger.info("zone_alert_tts started — engine: Piper")
    logger.info("Output root: %s  |  Interval: %ds", ZONES_ROOT, INTERVAL)

    while running:
        try:
            zones = _load_zones(db)
            logger.info("Loaded %d zones — processing NWS + FL511 traffic + airport weather", len(zones))
            process_nws_alerts(db, zones, tts)
            process_traffic(db, zones, tts)
            process_airport_weather(db, zones, tts)
        except Exception as e:
            logger.exception("Unexpected error in main loop: %s", e)
        if running:
            time.sleep(INTERVAL)

    client.close()
    logger.info("zone_alert_tts stopped.")


def run_once():
    """Process all pending alerts and traffic once, writing per-zone progress."""
    tts    = TTSService()
    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]
    db["zone_alert_wavs"].create_index(
        [("source_type", 1), ("source_id", 1), ("zone", 1)], unique=True
    )
    zones = _load_zones(db)
    logger.info("run_once — %d zones loaded", len(zones))

    progress = {
        "run_id":     datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "phase":      "nws",
        "zones":      {},
    }
    _write_progress(progress)

    process_nws_alerts(db, zones, tts, progress=progress)

    progress["phase"] = "traffic"
    _write_progress(progress)

    process_traffic(db, zones, tts, progress=progress)

    progress["phase"] = "airport"
    _write_progress(progress)

    process_airport_weather(db, zones, tts, progress=progress)

    progress["phase"] = "complete"
    progress["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_progress(progress)

    client.close()
    logger.info("run_once complete.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        run_once()
    else:
        main()
