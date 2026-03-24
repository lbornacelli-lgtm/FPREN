#!/usr/bin/env python3
"""
airport_delays_fetcher.py
--------------------------
Fetches real-time FAA airport delay data for Florida airports plus
key connection airports (Atlanta, Charlotte) and stores in MongoDB.
Also generates ElevenLabs TTS WAV files for each airport with active delays.

Data source: FAA Airport Status Web Service (ASWS)
  https://soa.smext.faa.gov/asws/api/airport/status/{ICAO}

Airports monitored:
  Florida: KGNV, KMCO, KTPA, KMIA, KFLL, KJAX, KTLH, KPNS, KRSW,
           KPBI, KDAB, KSRQ, KEYW, KOCF, KLAL
  Connections: KATL (Atlanta), KCLT (Charlotte)

Runs every 15 minutes.
WAV files saved to: weather_station/audio/airport_weather/{ICAO}.wav
"""

import hashlib
import logging
import os
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = "weather_rss"

ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

FAA_API = "https://soa.smext.faa.gov/asws/api/airport/status/{icao}"
HEADERS  = {"User-Agent": "BeaconWeatherStation/1.0 (fpren@localhost)"}

INTERVAL = 900  # 15 minutes

# Base audio output path (relative to project root)
AUDIO_BASE = os.getenv(
    "AUDIO_BASE",
    "/home/ufuser/Fpren-main/weather_station/audio"
)

# Airports to monitor
AIRPORTS = [
    # Florida airports
    {"icao": "KGNV", "name": "Gainesville Regional",          "zone": "north_florida",   "state": "FL"},
    {"icao": "KJAX", "name": "Jacksonville International",     "zone": "north_florida",   "state": "FL"},
    {"icao": "KTLH", "name": "Tallahassee International",      "zone": "north_florida",   "state": "FL"},
    {"icao": "KPNS", "name": "Pensacola International",        "zone": "north_florida",   "state": "FL"},
    {"icao": "KOCF", "name": "Ocala International",            "zone": "north_florida",   "state": "FL"},
    {"icao": "KMCO", "name": "Orlando International",          "zone": "central_florida", "state": "FL"},
    {"icao": "KDAB", "name": "Daytona Beach International",    "zone": "central_florida", "state": "FL"},
    {"icao": "KTPA", "name": "Tampa International",            "zone": "central_florida", "state": "FL"},
    {"icao": "KSRQ", "name": "Sarasota Bradenton International","zone": "central_florida","state": "FL"},
    {"icao": "KLAL", "name": "Lakeland Linder International",  "zone": "central_florida", "state": "FL"},
    {"icao": "KMIA", "name": "Miami International",            "zone": "south_florida",   "state": "FL"},
    {"icao": "KFLL", "name": "Fort Lauderdale Hollywood",      "zone": "south_florida",   "state": "FL"},
    {"icao": "KPBI", "name": "Palm Beach International",       "zone": "south_florida",   "state": "FL"},
    {"icao": "KRSW", "name": "Southwest Florida International","zone": "south_florida",   "state": "FL"},
    {"icao": "KEYW", "name": "Key West International",         "zone": "south_florida",   "state": "FL"},
    # Key connection airports for Gainesville travelers
    {"icao": "KATL", "name": "Atlanta Hartsfield-Jackson",     "zone": "all_florida",     "state": "GA"},
    {"icao": "KCLT", "name": "Charlotte Douglas International", "zone": "all_florida",     "state": "NC"},
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [airport_delays] %(levelname)s: %(message)s",
)
logger = logging.getLogger("airport_delays")


# ---------------------------------------------------------------------------
# FAA API fetch
# ---------------------------------------------------------------------------

def fetch_airport_status(icao: str) -> dict:
    """Fetch FAA airport status for a given ICAO code."""
    url = FAA_API.format(icao=icao)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("FAA fetch failed for %s: %s", icao, e)
        return {}


def parse_delays(data: dict) -> dict:
    """Parse FAA status response into a clean delay record."""
    delays = data.get("Status", [])
    ground_delay   = None
    ground_stop    = None
    arrival_delay  = None
    departure_delay = None
    closure        = None

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

    has_delay = any([ground_delay, ground_stop, arrival_delay,
                     departure_delay, closure])

    return {
        "has_delay":       has_delay,
        "ground_delay":    ground_delay,
        "ground_stop":     ground_stop,
        "arrival_delay":   arrival_delay,
        "departure_delay": departure_delay,
        "closure":         closure,
        "raw_delays":      delays,
        "weather":         data.get("Weather", {}),
        "visibility":      data.get("Visibility", ""),
        "sky_conditions":  data.get("Sky", ""),
        "temperature":     data.get("Temp", ""),
        "wind":            data.get("Wind", ""),
    }


# ---------------------------------------------------------------------------
# TTS text builder
# ---------------------------------------------------------------------------

def build_delay_text(airport: dict, delay_info: dict) -> str:
    """Build a spoken TTS text for the airport delay report."""
    name  = airport["name"]
    icao  = airport["icao"]
    state = airport["state"]

    parts = []

    # Special intro for connection airports
    if icao in ("KATL", "KCLT"):
        parts.append(f"Connection airport update for Gainesville travelers.")

    if not delay_info["has_delay"]:
        parts.append(f"{name} airport in {state} is currently operating normally with no reported delays.")
        return " ".join(parts)

    parts.append(f"Airport delay alert for {name}.")

    gd = delay_info.get("ground_delay")
    if gd:
        reason = gd.get("Reason", "")
        avg    = gd.get("Avg", "")
        parts.append(
            f"Ground delay in effect{f' due to {reason}' if reason else ''}."
            f"{f' Average delay: {avg}.' if avg else ''}"
        )

    gs = delay_info.get("ground_stop")
    if gs:
        reason = gs.get("Reason", "")
        parts.append(f"Ground stop in effect{f' due to {reason}' if reason else ''}.")

    ad = delay_info.get("arrival_delay")
    if ad:
        reason = ad.get("Reason", "")
        avg    = ad.get("Avg", "")
        parts.append(
            f"Arrival delays{f' due to {reason}' if reason else ''}."
            f"{f' Average: {avg}.' if avg else ''}"
        )

    dd = delay_info.get("departure_delay")
    if dd:
        reason = dd.get("Reason", "")
        parts.append(f"Departure delays{f' due to {reason}' if reason else ''}.")

    cl = delay_info.get("closure")
    if cl:
        parts.append("Airport closure in effect.")

    wx = delay_info.get("weather", {})
    if wx:
        wx_text = wx.get("Weather", {}).get("Temp", "")
        if wx_text:
            parts.append(f"Current conditions: {wx_text}.")

    parts.append("Check with your airline for the latest flight information.")
    return " ".join(parts)


def build_normal_text(airport: dict) -> str:
    """Build a spoken report for airports with no delays."""
    return (
        f"{airport['name']} airport is currently operating normally "
        f"with no F.A.A. reported delays or ground stops."
    )


# ---------------------------------------------------------------------------
# TTS synthesis
# ---------------------------------------------------------------------------

def synthesise_elevenlabs(text: str, path: str):
    from elevenlabs.client import ElevenLabs
    from elevenlabs import VoiceSettings
    from pydub import AudioSegment

    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    audio  = client.text_to_speech.convert(
        voice_id=ELEVENLABS_VOICE_ID,
        text=text,
        model_id="eleven_turbo_v2",
        voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75),
    )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_mp3 = path + ".tmp.mp3"
    try:
        with open(tmp_mp3, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        AudioSegment.from_mp3(tmp_mp3).export(path, format="wav")
        logger.info("ElevenLabs WAV: %s", path)
    finally:
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)


def synthesise_piper(text: str, path: str):
    from piper import PiperVoice
    import wave

    voice_model = os.getenv(
        "PIPER_VOICE_MODEL",
        "/home/ufuser/Fpren-main/weather_station/voices/en_US-amy-medium.onnx"
    )
    voice = PiperVoice.load(voice_model)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(voice.config.sample_rate)
        for chunk in voice.synthesize(text):
            wf.writeframes(chunk.audio_int16_bytes)
    os.replace(tmp, path)
    logger.info("Piper WAV: %s", path)


def synthesise(text: str, path: str):
    if ELEVENLABS_API_KEY:
        synthesise_elevenlabs(text, path)
    else:
        synthesise_piper(text, path)


# ---------------------------------------------------------------------------
# WAV output paths
# ---------------------------------------------------------------------------

def get_wav_paths(airport: dict, has_delay: bool) -> list:
    """
    Return list of WAV output paths for this airport.
    - airport_weather/{ICAO}.wav       — current conditions (always)
    - zones/all_florida/airport_weather/{ICAO}.wav
    - zones/{zone}/airport_weather/{ICAO}.wav  (if delay active)
    """
    icao = airport["icao"]
    zone = airport["zone"]
    fname = f"{icao}.wav"

    paths = [
        os.path.join(AUDIO_BASE, "airport_weather", fname),
        os.path.join(AUDIO_BASE, "zones", "all_florida", "airport_weather", fname),
    ]

    if zone != "all_florida":
        paths.append(os.path.join(AUDIO_BASE, "zones", zone, "airport_weather", fname))

    return paths


# ---------------------------------------------------------------------------
# Main fetch loop
# ---------------------------------------------------------------------------

def fetch_and_store(db):
    now        = datetime.now(timezone.utc)
    col        = db["airport_delays"]
    wav_col    = db["airport_delay_wavs"]
    generated  = 0
    delayed    = 0

    for airport in AIRPORTS:
        icao = airport["icao"]
        data = fetch_airport_status(icao)
        if not data:
            continue

        delay_info = parse_delays(data)
        has_delay  = delay_info["has_delay"]

        if has_delay:
            delayed += 1

        # Store in MongoDB
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

        # Build TTS text
        if has_delay:
            text = build_delay_text(airport, delay_info)
        else:
            text  = build_normal_text(airport)

        # Check if we need to regenerate WAV
        text_hash = hashlib.md5(text.encode()).hexdigest()
        existing  = wav_col.find_one({"icao": icao})
        if existing and existing.get("text_hash") == text_hash:
            continue  # No change — skip TTS

        # Generate WAVs for all zone paths
        wav_paths = get_wav_paths(airport, has_delay)
        success   = True
        for wav_path in wav_paths:
            try:
                synthesise(text, wav_path)
            except Exception as e:
                logger.error("WAV failed for %s at %s: %s", icao, wav_path, e)
                success = False

        if success:
            wav_col.update_one(
                {"icao": icao},
                {"$set": {
                    "icao":        icao,
                    "name":        airport["name"],
                    "has_delay":   has_delay,
                    "text":        text,
                    "text_hash":   text_hash,
                    "wav_paths":   wav_paths,
                    "tts_engine":  "ElevenLabs" if ELEVENLABS_API_KEY else "Piper",
                    "generated_at": now,
                }},
                upsert=True,
            )
            generated += 1
            logger.info("%s (%s) — delay=%s — WAV generated", icao, airport["name"], has_delay)

    logger.info(
        "Cycle complete — %d airports checked, %d with delays, %d WAVs generated",
        len(AIRPORTS), delayed, generated
    )


def main():
    logger.info("Airport delays fetcher starting")
    logger.info("TTS engine: %s", "ElevenLabs" if ELEVENLABS_API_KEY else "Piper")
    logger.info("Monitoring %d airports", len(AIRPORTS))

    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]

    db["airport_delays"].create_index("icao", unique=True)
    db["airport_delay_wavs"].create_index("icao", unique=True)

    while True:
        try:
            fetch_and_store(db)
        except Exception as e:
            logger.error("Unexpected error: %s", e)
        logger.info("Sleeping %d seconds...", INTERVAL)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
