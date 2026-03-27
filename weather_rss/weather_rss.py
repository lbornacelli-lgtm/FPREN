#!/usr/bin/env python3
"""
weather_rss.py

Polls NOAA current_obs XML feeds for Florida ASOS stations and saves
new observations to disk. Skips unchanged observations and purges files
older than KEEP_DAYS.
"""

import logging
import os
import signal
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Configuration ────────────────────────────────────────────────────────────

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/home/ufuser/Fpren-main/weather_rss/feeds")
FETCH_INTERVAL = int(os.environ.get("FETCH_INTERVAL", 900))   # seconds (default 15 min)
KEEP_DAYS      = int(os.environ.get("KEEP_DAYS", 7))
REQUEST_TIMEOUT = 10  # seconds per station request

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("weather_rss")

# ── Florida ASOS stations ─────────────────────────────────────────────────────

RSS_FEEDS = {
    # North Florida
    "Gainesville":     "https://w1.weather.gov/xml/current_obs/KGNV.xml",
    "Ocala":           "https://w1.weather.gov/xml/current_obs/KOCF.xml",
    "Palatka":         "https://w1.weather.gov/xml/current_obs/KPAK.xml",
    "Jacksonville":    "https://w1.weather.gov/xml/current_obs/KJAX.xml",
    "Tallahassee":     "https://w1.weather.gov/xml/current_obs/KTLH.xml",
    "Pensacola":       "https://w1.weather.gov/xml/current_obs/KPNS.xml",
    "Panama_City":     "https://w1.weather.gov/xml/current_obs/KECP.xml",
    # Central Florida
    "Orlando":         "https://w1.weather.gov/xml/current_obs/KMCO.xml",
    "Daytona_Beach":   "https://w1.weather.gov/xml/current_obs/KDAB.xml",
    "Tampa":           "https://w1.weather.gov/xml/current_obs/KTPA.xml",
    "Sarasota":        "https://w1.weather.gov/xml/current_obs/KSRQ.xml",
    "Lakeland":        "https://w1.weather.gov/xml/current_obs/KLAL.xml",
    # South Florida
    "Fort_Myers":      "https://w1.weather.gov/xml/current_obs/KRSW.xml",
    "Fort_Lauderdale": "https://w1.weather.gov/xml/current_obs/KFLL.xml",
    "Miami":           "https://w1.weather.gov/xml/current_obs/KMIA.xml",
    "West_Palm_Beach": "https://w1.weather.gov/xml/current_obs/KPBI.xml",
    "Key_West":        "https://w1.weather.gov/xml/current_obs/KEYW.xml",
    # West Coast
    "St_Petersburg":   "https://w1.weather.gov/xml/current_obs/KSPG.xml",
    "Naples":          "https://w1.weather.gov/xml/current_obs/KAPF.xml",
}

# ── HTTP session with retry ───────────────────────────────────────────────────

def _build_session() -> requests.Session:
    """Return a session with automatic retries on transient failures."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://",  HTTPAdapter(max_retries=retry))
    return session

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_observation_time(xml_bytes: bytes) -> str | None:
    """Extract <observation_time_rfc822> from NOAA current_obs XML."""
    try:
        root = ET.fromstring(xml_bytes)
        el = root.find("observation_time_rfc822")
        return el.text.strip() if el is not None and el.text else None
    except ET.ParseError:
        return None


def cleanup_old_files() -> int:
    """Delete XML files older than KEEP_DAYS. Returns number of files deleted."""
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    deleted = 0
    for filename in os.listdir(OUTPUT_DIR):
        if not filename.endswith(".xml"):
            continue
        path = os.path.join(OUTPUT_DIR, filename)
        try:
            if datetime.fromtimestamp(os.path.getmtime(path)) < cutoff:
                os.remove(path)
                logger.debug("Deleted old file: %s", path)
                deleted += 1
        except OSError as e:
            logger.warning("Could not delete %s: %s", path, e)
    return deleted


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    session = _build_session()
    last_obs_time: dict[str, str] = {}
    running = True

    def _handle_shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received — stopping after current cycle.")
        running = False

    signal.signal(signal.SIGINT,  _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    logger.info("weather_rss starting. Polling %d stations every %ds.",
                len(RSS_FEEDS), FETCH_INTERVAL)

    while running:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved = skipped = errors = 0

        for city, url in RSS_FEEDS.items():
            if not running:
                break
            try:
                response = session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                content = response.content

                obs_time = get_observation_time(content)
                if obs_time and last_obs_time.get(city) == obs_time:
                    skipped += 1
                    continue

                last_obs_time[city] = obs_time or timestamp
                path = os.path.join(OUTPUT_DIR, f"{city}_{timestamp}.xml")
                with open(path, "wb") as f:
                    f.write(content)
                logger.info("Saved %-20s obs_time: %s", city, obs_time)
                saved += 1

            except requests.exceptions.Timeout:
                logger.warning("Timeout fetching %s — skipping.", city)
                errors += 1
            except requests.exceptions.RequestException as e:
                logger.error("Request error for %s: %s", city, e)
                errors += 1
            except OSError as e:
                logger.error("File write error for %s: %s", city, e)
                errors += 1

        deleted = cleanup_old_files()
        logger.info(
            "Cycle complete — saved: %d  skipped: %d  errors: %d  purged: %d",
            saved, skipped, errors, deleted
        )

        if running:
            time.sleep(FETCH_INTERVAL)

    logger.info("weather_rss stopped.")


if __name__ == "__main__":
    run()
