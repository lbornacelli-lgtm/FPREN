#!/usr/bin/env python3
"""
airport_delays_fetcher.py
--------------------------
Fetches real-time FAA airport delay data for Florida airports plus
key connection airports (Atlanta, Charlotte) and stores in MongoDB.
Generates Piper or ElevenLabs TTS WAV files for airports with active delays.

Data source: FAA Airport Status Web Service (ASWS)
  https://soa.smext.faa.gov/asws/api/airport/status/{ICAO}

Runs every 15 minutes. WAV files saved to:
  weather_station/audio/airport_weather/{ICAO}.wav
"""

import hashlib
import logging
import os
import signal
import time
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pymongo import MongoClient

from weather_station.core.tts_service import TTSService

# ── Configuration ─────────────────────────────────────────────────────────────

MONGO_URI  = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME    = "weather_rss"

FAA_API    = "https://soa.smext.faa.gov/asws/api/airport/status/{icao}"
HEADERS    = {"User-Agent": "FPRENWeatherStation/1.0 (contact@fpren.example.com)"}
INTERVAL   = int(os.getenv("FETCH_INTERVAL", 900))
AUDIO_BASE = os.getenv(
    "AUDIO_BASE",
    "/home/ufuser/Fpren-main/weather_station/audio",
)

# ── Airports ──────────────────────────────────────────────────────────────────

AIRPORTS = [
    # North Florida
    {"icao": "KGNV", "name": "Gainesville Regional",           "zone": "north_florida",   "state": "FL"},
    {"icao": "KJAX", "name": "Jacksonville International",      "zone": "north_florida",   "state": "FL"},
    {"icao": "KTLH", "name": "Tallahassee International",       "zone": "north_florida",   "state": "FL"},
    {"icao": "KPNS", "name": "Pensacola International",         "zone": "north_florida",   "state": "FL"},
    {"icao": "KOCF", "name": "Ocala International",             "zone": "north_florida",   "state": "FL"},
    # Central Florida
    {"icao": "KMCO", "name": "Orlando International",           "zone": "central_florida", "state": "FL"},
    {"icao": "KDAB", "name": "Daytona Beach International",     "zone": "central_florida", "state": "FL"},
    {"icao": "KTPA", "name": "Tampa International",             "zone": "central_florida", "state": "FL"},
    {"icao": "KSRQ", "name": "Sarasota Bradenton International","zone": "central_florida", "state": "FL"},
    {"icao": "KLAL", "name": "Lakeland Linder International",   "zone": "central_florida", "state": "FL"},
    # South Florida
    {"icao": "KMIA", "name": "Miami International",             "zone": "south_florida",   "state": "FL"},
    {"icao": "KFLL", "name": "Fort Lauderdale Hollywood",       "zone": "south_florida",   "state": "FL"},
    {"icao": "KPBI", "name": "Palm Beach International",        "zone": "south_florida",   "state": "FL"},
    {"icao": "KRSW", "name": "Southwest Florida International", "zone": "south_florida",   "state": "FL"},
    {"icao": "KEYW", "name": "Key West International",          "zone": "south_florida",   "state": "FL"},
    # Key connections for Gainesville travelers
    {"icao": "KATL", "name": "Atlanta Hartsfield-Jackson",      "zone": "all_florida",     "state": "GA"},
    {"icao": "KCLT", "name": "Charlotte Douglas International",  "zone": "all_florida",     "state": "NC"},
]

CONNECTION_AIRPORTS = {"KATL", "KCLT"}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [airport_delays] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("airport_delays")

# ── HTTP session ──────────────────────────────────────────────────────────────

def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://",  HTTPAdapter(max_retries=retry))
    return session

# ── FAA API ───────────────────────────────────────────────────────────────────

def fetch_airport_status(session: requests.Session, icao: str) -> dict:
    """Fetch FAA airport status for a given ICAO code."""
    url = FAA_API.format(icao=icao)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        logger.warning("Timeout fetching %s", icao)
    except requests.exceptions.RequestException as e:
        logger.error("FAA fetch failed for %s: %s", icao, e)
    return {}


def parse_delays(data: dict) -> dict:
    """Parse FAA status response into a clean delay record."""
    delays          = data.get("Status", [])
    ground_delay    = None
    ground_stop     = None
    arrival_delay   = None
    departure_delay = None
    closure         = None

    for d in delays:
        dtype = d.get("Type", "").lower()
        if "ground delay" in dtype:
            ground_delay = d
        elif "ground stop" in dtype:
            ground_stop = d
        elif "arrival" in dtype:
            arrival_delay = d
        elif "departure" in dtype:
            departure_delay = d
        elif "closure" in dtype or "closed" in dtype:
            closure = d

    return {
        "has_delay":        any([ground_delay, ground_stop, arrival_delay,
                                 departure_delay, closure]),
        "ground_delay":     ground_delay,
        "ground_stop":      ground_stop,
        "arrival_delay":    arrival_delay,
        "departure_delay":  departure_delay,
        "closure":          closure,
        "raw_delays":       delays,
        "weather":          data.get("Weather", {}),
        "visibility":       data.get("Visibility", ""),
        "sky_conditions":   data.get("Sky", ""),
        "temperature":      data.get("Temp", ""),
        "wind":             data.get("Wind", ""),
    }

# ── TTS text builders ─────────────────────────────────────────────────────────

def build_delay_text(airport: dict, delay_info: dict) -> str:
    """Build spoken TTS text for an airport with active delays."""
    name  = airport["name"]
    icao  = airport["icao"]
    state = airport["state"]
    parts = []

    if icao in CONNECTION_AIRPORTS:
        parts.append("Connection airport update for Gainesville travelers.")

    parts.append(f"Airport delay alert for {name}.")

    for key, label in (
        ("ground_delay",    "Ground delay"),
        ("ground_stop",     "Ground stop"),
        ("arrival_delay",   "Arrival delays"),
        ("departure_delay", "Departure delays"),
    ):
        item = delay_info.get(key)
        if not item:
            continue
        reason = item.get("Reason", "")
        avg    = item.get("Avg", "")
        text   = f"{label} in effect{f' due to {reason}' if reason else ''}."
        if avg:
            text += f" Average delay: {avg}."
        parts.append(text)

    if delay_info.get("closure"):
        parts.append("Airport closure in effect.")

    parts.append("Check with your airline for the latest flight information.")
    return " ".join(parts)


def build_normal_text(airport: dict) -> str:
    """Build spoken TTS text for an airport with no delays."""
    return (
        f"{airport['name']} airport is currently operating normally "
        f"with no F.A.A. reported delays or ground stops."
    )



# ── MP3 paths ─────────────────────────────────────────────────────────────────

def get_wav_paths(airport: dict) -> list[str]:
    """Return all MP3 output paths for this airport across zones."""
    icao  = airport["icao"]
    zone  = airport["zone"]
    fname = f"{icao}.mp3"

    paths = [
        os.path.join(AUDIO_BASE, "airport_weather", fname),
        os.path.join(AUDIO_BASE, "zones", "all_florida", "airport_weather", fname),
    ]
    if zone != "all_florida":
        paths.append(
            os.path.join(AUDIO_BASE, "zones", zone, "airport_weather", fname)
        )
    return paths

# ── Main fetch loop ───────────────────────────────────────────────────────────

def fetch_and_store(session: requests.Session, db, tts: TTSService):
    now       = datetime.now(timezone.utc)
    col       = db["airport_delays"]
    wav_col   = db["airport_delay_wavs"]
    generated = delayed = 0

    for airport in AIRPORTS:
        icao = airport["icao"]
        data = fetch_airport_status(session, icao)
        if not data:
            continue

        delay_info = parse_delays(data)
        has_delay  = delay_info["has_delay"]
        if has_delay:
            delayed += 1

        col.update_one(
            {"icao": icao},
            {"$set": {
                "icao":       icao,
                "name":       airport["name"],
                "zone":       airport["zone"],
                "state":      airport["state"],
                "has_delay":  has_delay,
                "delays":     delay_info["raw_delays"],
                "weather":    delay_info["weather"],
                "fetched_at": now,
            }},
            upsert=True,
        )

        text      = build_delay_text(airport, delay_info) if has_delay else build_normal_text(airport)
        text_hash = hashlib.md5(text.encode()).hexdigest()
        existing  = wav_col.find_one({"icao": icao})

        if existing and existing.get("text_hash") == text_hash:
            continue  # Text unchanged — skip TTS

        mp3_paths = get_wav_paths(airport)
        success   = True
        for mp3_path in mp3_paths:
            try:
                tts.say(text, output_file=mp3_path)
            except Exception as e:
                logger.error("MP3 generation failed for %s at %s: %s", icao, mp3_path, e)
                success = False

        if success:
            wav_col.update_one(
                {"icao": icao},
                {"$set": {
                    "icao":         icao,
                    "name":         airport["name"],
                    "has_delay":    has_delay,
                    "text":         text,
                    "text_hash":    text_hash,
                    "wav_paths":    mp3_paths,
                    "tts_engine":   "ElevenLabs",
                    "generated_at": now,
                }},
                upsert=True,
            )
            generated += 1
            logger.info("%s — delay=%-5s — MP3 generated", icao, has_delay)

    logger.info(
        "Cycle complete — %d airports, %d delayed, %d WAVs generated",
        len(AIRPORTS), delayed, generated,
    )


def main():
    tts     = TTSService()
    client  = MongoClient(MONGO_URI)
    db      = client[DB_NAME]
    session = _build_session()
    running = True

    db["airport_delays"].create_index("icao", unique=True)
    db["airport_delay_wavs"].create_index("icao", unique=True)

    def _shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received — stopping.")
        running = False

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Airport delays fetcher starting.")
    logger.info("TTS engine  : ElevenLabs via LiteLLM")
    logger.info("Airports    : %d", len(AIRPORTS))
    logger.info("Interval    : %ds", INTERVAL)

    while running:
        try:
            fetch_and_store(session, db, tts)
        except Exception as e:
            logger.exception("Unexpected error in fetch cycle: %s", e)
        if running:
            logger.info("Sleeping %ds...", INTERVAL)
            time.sleep(INTERVAL)

    client.close()
    logger.info("Airport delays fetcher stopped.")


if __name__ == "__main__":
    main()
