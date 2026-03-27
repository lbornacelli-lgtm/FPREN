#!/usr/bin/env python3
"""
fetch_weather_rss.py

One-shot script to fetch NWS API data for Florida alerts and
Gainesville forecasts, saving each response as a dated JSON file.

Usage:
    python3 scripts/fetch_weather_rss.py
"""

import logging
import os
import sys
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Configuration ─────────────────────────────────────────────────────────────

OUTPUT_FOLDER   = os.environ.get("WEATHER_DATA_DIR", os.path.expanduser("~/weather_data"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 15))

HEADERS = {
    "User-Agent": "FPREN-WeatherStation/1.0 (contact@fpren.example.com)",
    "Accept": "application/geo+json",
}

SOURCES = {
    "fl_alerts":            "https://api.weather.gov/alerts/active?area=FL",
    "gainesville_forecast": "https://api.weather.gov/gridpoints/JAX/48,30/forecast",
    "gainesville_hourly":   "https://api.weather.gov/gridpoints/JAX/48,30/forecast/hourly",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fetch_weather_rss")

# ── HTTP session with retry ───────────────────────────────────────────────────

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

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_and_save(session: requests.Session, name: str, url: str,
                   date_str: str) -> bool:
    """Fetch a single NWS endpoint and save to a dated JSON file.

    Returns True on success, False on any failure.
    """
    output_file = os.path.join(OUTPUT_FOLDER, f"{name}_{date_str}.json")

    # Skip if already fetched today
    if os.path.exists(output_file):
        logger.info("Already fetched today — skipping: %s", output_file)
        return True

    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
        response.raise_for_status()

        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        with open(output_file, "wb") as f:
            f.write(response.content)
        logger.info("Saved %-28s → %s", name, output_file)
        return True

    except requests.exceptions.Timeout:
        logger.error("Timeout fetching %s", name)
    except requests.exceptions.HTTPError as e:
        logger.error("HTTP error for %s: %s", name, e)
    except requests.exceptions.RequestException as e:
        logger.error("Request error for %s: %s", name, e)
    except OSError as e:
        logger.error("File write error for %s: %s", name, e)

    return False


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    """Fetch all sources. Returns exit code (0 = all ok, 1 = any failure)."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    session  = _build_session()
    failures = 0

    logger.info("Fetching %d NWS sources for %s ...", len(SOURCES), date_str)

    for name, url in SOURCES.items():
        if not fetch_and_save(session, name, url, date_str):
            failures += 1

    if failures:
        logger.warning("Done — %d/%d sources failed.", failures, len(SOURCES))
    else:
        logger.info("Done — all %d sources saved successfully.", len(SOURCES))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
