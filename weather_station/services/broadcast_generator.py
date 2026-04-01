#!/usr/bin/env python3
# weather_station/services/broadcast_generator.py
"""
broadcast_generator.py
-----------------------
Generates AI broadcast scripts from live MongoDB data and converts
them to audio via TTS (ElevenLabs for quality, Piper as fallback).

Runs as a long-lived daemon (main()) that calls run_all_zones() every
BROADCAST_INTERVAL seconds (default: 1800 = 30 minutes).  Handles
SIGINT and SIGTERM for clean shutdown.

Can also be imported and called directly: run_all_zones(db, tts).

Output: zones/{zone}/weather_report/broadcast_{timestamp}.mp3
"""

import logging
import os
import signal
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from pymongo import MongoClient
from weather_station.services.ai_client import chat, is_configured
from weather_station.services.elevenlabs_tts import say as el_say, is_configured as el_configured
from weather_station.core.tts_service import TTSService

logger = logging.getLogger("broadcast_generator")

MONGO_URI  = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME    = "weather_rss"
ZONES_ROOT = os.getenv("ZONES_ROOT",
             "/home/ufuser/Fpren-main/weather_station/audio/zones")

# ── System prompt ─────────────────────────────────────────────────────────────

_BROADCAST_SYSTEM = """You are a professional weather radio announcer for FPREN,
Florida Public Radio Emergency Network. Generate a concise spoken broadcast
from the provided Florida weather alert data.
Format:
- Open with station ID: "This is FPREN, Florida Public Radio Emergency Network."
- Lead with any active critical alerts
- Summarize elevated alerts briefly
- Close with: "Stay weather aware. This has been FPREN."
Keep it under 150 words. Plain text only, no formatting."""


# ── Data gathering ────────────────────────────────────────────────────────────

def _gather_broadcast_data(db, zone_id: str, max_alerts: int = 8) -> dict:
    """Pull current alert data from MongoDB for broadcast generation."""
    try:
        # Get active alerts for this zone
        wavs = list(db["zone_alert_wavs"].find(
            {"zone": zone_id},
            {"event": 1, "ai_severity": 1, "area_desc": 1,
             "alert_folder": 1, "generated_at": 1, "_id": 0}
        ).sort("generated_at", -1).limit(max_alerts))

        critical = [w for w in wavs if w.get("ai_severity") == "critical"]
        elevated = [w for w in wavs if w.get("ai_severity") == "elevated"]
        routine  = [w for w in wavs if w.get("ai_severity") == "routine"]

        return {
            "zone_id":  zone_id,
            "critical": critical,
            "elevated": elevated,
            "routine":  routine,
            "total":    len(wavs),
            "timestamp": datetime.now(timezone.utc).strftime("%B %d at %I:%M %p UTC"),
        }
    except Exception as e:
        logger.error("Failed to gather broadcast data for %s: %s", zone_id, e)
        return {"zone_id": zone_id, "critical": [], "elevated": [],
                "routine": [], "total": 0,
                "timestamp": datetime.now(timezone.utc).strftime("%B %d at %I:%M %p UTC")}


def _build_prompt(data: dict) -> str:
    """Build the LiteLLM prompt from gathered data."""
    lines = [f"Zone: {data['zone_id']}", f"Time: {data['timestamp']}"]

    if data["critical"]:
        lines.append("CRITICAL ALERTS:")
        for a in data["critical"]:
            lines.append(f"  - {a.get('event','')} in {a.get('area_desc','')[:60]}")

    if data["elevated"]:
        lines.append("ELEVATED ALERTS:")
        for a in data["elevated"]:
            lines.append(f"  - {a.get('event','')} in {a.get('area_desc','')[:60]}")

    if data["routine"]:
        lines.append(f"ROUTINE ALERTS: {len(data['routine'])} active")

    if data["total"] == 0:
        lines.append("No active alerts.")

    return "\n".join(lines)


# ── Script generation ─────────────────────────────────────────────────────────

def generate_script(db, zone_id: str) -> str:
    """Generate broadcast script for a zone using LiteLLM."""
    data   = _gather_broadcast_data(db, zone_id)
    prompt = _build_prompt(data)

    if not is_configured():
        # Fallback script
        if data["total"] == 0:
            return (f"This is FPREN, Florida Public Radio Emergency Network. "
                    f"No active weather alerts for {zone_id.replace('_', ' ').title()} "
                    f"at this time. Stay weather aware. This has been FPREN.")
        alert_count = data["total"]
        return (f"This is FPREN, Florida Public Radio Emergency Network. "
                f"There are currently {alert_count} active weather alerts "
                f"for {zone_id.replace('_', ' ').title()}. "
                f"Please monitor local conditions. This has been FPREN.")

    try:
        script = chat(prompt, system=_BROADCAST_SYSTEM, max_tokens=200)
        logger.info("AI broadcast script generated for %s (%d chars)", zone_id, len(script))
        return script
    except Exception as e:
        logger.error("Broadcast script generation failed for %s: %s", zone_id, e)
        return (f"This is FPREN. Weather monitoring is active for "
                f"{zone_id.replace('_', ' ').title()}. Stay weather aware. This has been FPREN.")


# ── Audio output ──────────────────────────────────────────────────────────────

def generate_broadcast_audio(db, zone_id: str, tts: TTSService = None) -> str | None:
    """
    Generate broadcast script and convert to audio.
    Uses ElevenLabs if configured (better quality for broadcasts),
    falls back to gTTS.
    Returns output file path or None on failure.
    """
    script = generate_script(db, zone_id)
    if not script:
        return None

    # Output path
    ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir  = os.path.join(ZONES_ROOT, zone_id, "weather_report")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"broadcast_{ts}.mp3")

    # Try ElevenLabs first (higher quality for broadcasts)
    if el_configured():
        result = el_say(script, out_path)
        if result:
            logger.info("Broadcast audio (ElevenLabs) → %s", out_path)
            return out_path
        logger.warning("ElevenLabs failed — falling back to gTTS")

    # Fall back to Piper
    try:
        _tts = tts or TTSService()
        _tts.say(script, output_file=out_path)
        logger.info("Broadcast audio (Piper) → %s", out_path)
        return out_path
    except Exception as e:
        logger.error("Broadcast audio generation failed for %s: %s", zone_id, e)
        return None


# ── Standalone runner ─────────────────────────────────────────────────────────

def run_all_zones(db, tts: TTSService = None):
    """Generate broadcast audio for all zones."""
    zones = list(db["zone_definitions"].find({}, {"zone_id": 1, "_id": 0}))
    for z in zones:
        zone_id = z["zone_id"]
        try:
            path = generate_broadcast_audio(db, zone_id, tts)
            if path:
                logger.info("Broadcast ready [%s]: %s", zone_id, os.path.basename(path))
        except Exception as e:
            logger.error("Broadcast failed [%s]: %s", zone_id, e)


BROADCAST_INTERVAL = int(os.getenv("BROADCAST_INTERVAL", 1800))  # seconds


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s [broadcast_generator] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    client  = MongoClient(MONGO_URI)
    db      = client[DB_NAME]
    tts     = TTSService()
    running = True
    wakeup  = threading.Event()

    def _shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal %d received — stopping.", signum)
        running = False
        wakeup.set()  # unblock the sleep immediately

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(
        "broadcast_generator started — interval: %ds (%d min)",
        BROADCAST_INTERVAL, BROADCAST_INTERVAL // 60,
    )

    while running:
        try:
            run_all_zones(db, tts)
        except Exception as e:
            logger.exception("Unexpected error in broadcast loop: %s", e)
        if running:
            wakeup.wait(timeout=BROADCAST_INTERVAL)
            wakeup.clear()

    client.close()
    logger.info("broadcast_generator stopped.")


if __name__ == "__main__":
    main()
