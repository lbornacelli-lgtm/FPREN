#!/usr/bin/env python3
# weather_station/services/alert_fetcher_runner.py
"""
alert_fetcher_runner.py
-----------------------
Background service that runs all alert fetchers on a schedule:
  - NWS alerts  — existing pipeline (already running separately)
  - IPAWS/FEMA  — every IPAWS_INTERVAL seconds (default: 60)
  - County RSS  — every COUNTY_INTERVAL seconds (default: 120)

All fetchers write into weather_rss.nws_alerts.
zone_alert_tts.py reads from that collection and handles TTS + routing.

Usage:
    cd /home/ufuser/Fpren-main
    source venv/bin/activate
    python -m weather_station.services.alert_fetcher_runner

Or run directly:
    python weather_station/services/alert_fetcher_runner.py

Environment variables:
    MONGO_URI           — MongoDB connection string (default: localhost)
    IPAWS_INTERVAL      — seconds between IPAWS polls (default: 60)
    COUNTY_INTERVAL     — seconds between county RSS polls (default: 120)
    ALERT_FETCHER_LOG   — log file path
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

from pymongo import MongoClient

# Allow running as script or module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from weather_station.services.ipaws_fetcher import run_once as ipaws_run
from weather_station.services.county_rss_fetcher import run_once as county_run

# ── Config ────────────────────────────────────────────────────────────────────

MONGO_URI       = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME         = "weather_rss"
IPAWS_INTERVAL  = int(os.getenv("IPAWS_INTERVAL",  60))
COUNTY_INTERVAL = int(os.getenv("COUNTY_INTERVAL", 120))

LOG_FILE = os.getenv(
    "ALERT_FETCHER_LOG",
    "/home/ufuser/Fpren-main/logs/alert_fetcher.log",
)

# ── Logging ───────────────────────────────────────────────────────────────────

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [alert_fetcher] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("alert_fetcher_runner")

# ── Runner ────────────────────────────────────────────────────────────────────

def main():
    client  = MongoClient(MONGO_URI)
    db      = client[DB_NAME]
    running = True

    # Ensure index exists
    db["nws_alerts"].create_index("alert_id", unique=True, sparse=True)

    def _shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal — stopping fetcher runner.")
        running = False

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("alert_fetcher_runner started")
    logger.info("IPAWS interval: %ds | County RSS interval: %ds", IPAWS_INTERVAL, COUNTY_INTERVAL)

    last_ipaws  = 0.0
    last_county = 0.0

    while running:
        now = time.monotonic()

        # --- IPAWS poll ---
        if now - last_ipaws >= IPAWS_INTERVAL:
            try:
                n = ipaws_run(db)
                if n:
                    logger.info("IPAWS: %d new/updated alert(s)", n)
                else:
                    logger.debug("IPAWS: no new alerts")
            except Exception as e:
                logger.error("IPAWS fetch error: %s", e)
            last_ipaws = time.monotonic()

        # --- County RSS poll ---
        if now - last_county >= COUNTY_INTERVAL:
            try:
                n = county_run(db)
                if n:
                    logger.info("County RSS: %d new/updated alert(s)", n)
                else:
                    logger.debug("County RSS: no new alerts")
            except Exception as e:
                logger.error("County RSS fetch error: %s", e)
            last_county = time.monotonic()

        time.sleep(5)  # tight loop with 5s tick so shutdown is responsive

    client.close()
    logger.info("alert_fetcher_runner stopped.")


if __name__ == "__main__":
    main()
