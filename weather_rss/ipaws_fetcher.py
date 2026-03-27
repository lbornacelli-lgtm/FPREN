#!/usr/bin/env python3
"""
ipaws_fetcher.py

Polls the IPAWS PUBLIC feed every 2 minutes, filters for Florida alerts
(SAME prefix 012) with priority flagging for Alachua County (SAME 012001),
and stores new alerts in MongoDB weather_rss.nws_alerts.

Feed URLs:
  Staging:    https://tdl.apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent/{timestamp}
  Production: https://apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent/{timestamp}

Set IPAWS_BASE_URL env var to switch between staging and production.
"""

import logging
import os
import signal
import time
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pymongo import MongoClient

# ── CAP namespace ─────────────────────────────────────────────────────────────

NS_CAP = "urn:oasis:names:tc:emergency:cap:1.2"

# ── SAME geocode constants ────────────────────────────────────────────────────

FLORIDA_SAME_PREFIX = "012"       # All Florida counties
ALACHUA_SAME_CODE   = "012001"    # Alachua County specifically

# ── Configuration ─────────────────────────────────────────────────────────────

IPAWS_BASE_URL = os.getenv(
    "IPAWS_BASE_URL",
    "https://tdl.apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent",
)
POLL_INTERVAL = int(os.getenv("IPAWS_POLL_INTERVAL", "120"))
MONGO_URI     = os.getenv("MONGO_URI", "mongodb://localhost:27017")
LOG_FILE      = os.getenv("LOG_FILE", "/home/ufuser/Fpren-main/weather_rss/logs/ipaws.log")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [IPAWSFetcher] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("IPAWSFetcher")

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

# ── CAP helpers ───────────────────────────────────────────────────────────────

def _text(elem, tag: str) -> str:
    child = elem.find(f"{{{NS_CAP}}}{tag}")
    return child.text.strip() if child is not None and child.text else ""


def _info_text(alert_elem, tag: str) -> str:
    child = alert_elem.find(f".//{{{NS_CAP}}}{tag}")
    return child.text.strip() if child is not None and child.text else ""


def _get_same_codes(alert_elem) -> list[str]:
    """Return all SAME geocode values from a CAP alert element."""
    codes = []
    for area in alert_elem.findall(f".//{{{NS_CAP}}}area"):
        for geocode in area.findall(f"{{{NS_CAP}}}geocode"):
            name  = geocode.findtext(f"{{{NS_CAP}}}valueName", "")
            value = geocode.findtext(f"{{{NS_CAP}}}value", "")
            if name == "SAME" and value:
                codes.append(value)
    return codes


def is_florida(alert_elem) -> bool:
    """Return True if any SAME geocode starts with 012 or areaDesc mentions Florida."""
    for code in _get_same_codes(alert_elem):
        if code.startswith(FLORIDA_SAME_PREFIX):
            return True
    for area in alert_elem.findall(f".//{{{NS_CAP}}}area"):
        desc = area.findtext(f"{{{NS_CAP}}}areaDesc", "")
        if ", FL" in desc or "Florida" in desc:
            return True
    return False


def is_alachua(alert_elem) -> bool:
    """Return True if the alert specifically covers Alachua County."""
    for code in _get_same_codes(alert_elem):
        if code == ALACHUA_SAME_CODE:
            return True
    for area in alert_elem.findall(f".//{{{NS_CAP}}}area"):
        desc = area.findtext(f"{{{NS_CAP}}}areaDesc", "")
        if "Alachua" in desc:
            return True
    return False


def parse_alert(alert_elem) -> dict:
    """Extract fields from a CAP <alert> element into a MongoDB document."""
    area_descs = [
        a.findtext(f"{{{NS_CAP}}}areaDesc", "").strip()
        for a in alert_elem.findall(f".//{{{NS_CAP}}}area")
    ]
    same_codes = _get_same_codes(alert_elem)
    alachua    = is_alachua(alert_elem)

    return {
        "alert_id":       _text(alert_elem, "identifier"),
        "event":          _info_text(alert_elem, "event"),
        "headline":       _info_text(alert_elem, "headline"),
        "description":    _info_text(alert_elem, "description"),
        "severity":       _info_text(alert_elem, "severity"),
        "area_desc":      "; ".join(filter(None, area_descs)),
        "same_codes":     same_codes,
        "alachua_county": alachua,
        "sender":         _text(alert_elem, "sender"),
        "sent":           _text(alert_elem, "sent"),
        "status":         _text(alert_elem, "status"),
        "msg_type":       _text(alert_elem, "msgType"),
        "source":         "IPAWS",
    }

# ── Fetch and store ───────────────────────────────────────────────────────────

def fetch_and_store(session: requests.Session, col, since_dt: datetime) -> int:
    """Fetch IPAWS alerts since `since_dt`, store new Florida ones.

    Returns count of newly stored alerts.
    """
    since_str = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{IPAWS_BASE_URL}/{since_str}"

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.warning("Timeout fetching IPAWS feed.")
        return 0
    except requests.exceptions.RequestException as e:
        logger.error("Fetch error: %s", e)
        return 0

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error("XML parse error: %s", e)
        return 0

    alert_elems = [
        child for child in root
        if child.tag in (f"{{{NS_CAP}}}alert", "alert")
    ]

    stored = alachua_count = 0

    for alert_elem in alert_elems:
        if not is_florida(alert_elem):
            continue

        data = parse_alert(alert_elem)
        if not data["alert_id"]:
            continue

        result = col.update_one(
            {"alert_id": data["alert_id"]},
            {"$setOnInsert": {
                **data,
                "tts_generated": False,
                "fetched_at":    datetime.now(timezone.utc),
            }},
            upsert=True,
        )

        if result.upserted_id:
            if data["alachua_county"]:
                alachua_count += 1
                logger.info(
                    "New ALACHUA alert: [%s] %s",
                    data["event"], data["headline"][:80],
                )
            else:
                logger.info(
                    "New FL alert: [%s] %s",
                    data["event"], data["headline"][:80],
                )
            stored += 1

    if stored:
        logger.info(
            "Stored %d new alert(s) — %d Alachua County.",
            stored, alachua_count,
        )

    return stored

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    client  = MongoClient(MONGO_URI)
    col     = client["weather_rss"]["nws_alerts"]
    session = _build_session()
    running = True

    def _shutdown(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received — stopping.")
        running = False

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("IPAWS fetcher started — poll=%ds  feed=%s", POLL_INTERVAL, IPAWS_BASE_URL)
    logger.info("Filtering: Florida (SAME 012*) | Alachua County (SAME %s)", ALACHUA_SAME_CODE)

    # On first run, look back 5 minutes to catch any recent alerts
    since_dt = datetime.now(timezone.utc) - timedelta(minutes=5)

    while running:
        now = datetime.now(timezone.utc)
        fetch_and_store(session, col, since_dt)
        since_dt = now

        if running:
            time.sleep(POLL_INTERVAL)

    client.close()
    logger.info("IPAWS fetcher stopped.")


if __name__ == "__main__":
    main()
