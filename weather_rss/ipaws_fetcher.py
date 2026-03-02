#!/usr/bin/env python3
"""
IPAWS (Integrated Public Alert and Warning System) fetcher.

Polls the IPAWS PUBLIC feed every 2 minutes, filters for Florida alerts
using SAME geocodes (prefix 012 = Florida FIPS state code), and stores
new alerts in MongoDB weather_rss.nws_alerts — the same collection used
by the NWS alert pipeline, so the alert_processor picks them up automatically.

Feed URLs:
  Staging:    https://tdl.apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent/{timestamp}
  Production: https://apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent/{timestamp}

Set IPAWS_BASE_URL env var to switch between staging and production.
"""
import os
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from pymongo import MongoClient

# CAP 1.2 namespace
NS_CAP = "urn:oasis:names:tc:emergency:cap:1.2"

# Florida FIPS state code — SAME codes start with 012
FLORIDA_SAME_PREFIX = "012"

IPAWS_BASE_URL = os.getenv(
    "IPAWS_BASE_URL",
    "https://tdl.apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent",
)
POLL_INTERVAL  = int(os.getenv("IPAWS_POLL_INTERVAL", "120"))  # 2 minutes
MONGO_URI      = os.getenv("MONGO_URI", "mongodb://localhost:27017")
LOG_FILE       = os.getenv("LOG_FILE", "/home/lh_admin/weather_rss/logs/ipaws.log")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [IPAWSFetcher] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("IPAWSFetcher")


def _text(elem, tag):
    """Return stripped text for a direct CAP child tag, or empty string."""
    child = elem.find(f"{{{NS_CAP}}}{tag}")
    return child.text.strip() if child is not None and child.text else ""


def _info_text(alert_elem, tag):
    """Return stripped text for a CAP info/* tag."""
    child = alert_elem.find(f".//{{{NS_CAP}}}{tag}")
    return child.text.strip() if child is not None and child.text else ""


def is_florida(alert_elem) -> bool:
    """
    Return True if any SAME geocode starts with 012 (Florida)
    or areaDesc contains ', FL' or 'Florida'.
    """
    for area in alert_elem.findall(f".//{{{NS_CAP}}}area"):
        for geocode in area.findall(f"{{{NS_CAP}}}geocode"):
            name  = geocode.findtext(f"{{{NS_CAP}}}valueName", "")
            value = geocode.findtext(f"{{{NS_CAP}}}value", "")
            if name == "SAME" and value.startswith(FLORIDA_SAME_PREFIX):
                return True
        desc_elem = area.find(f"{{{NS_CAP}}}areaDesc")
        if desc_elem is not None and desc_elem.text:
            if ", FL" in desc_elem.text or "Florida" in desc_elem.text:
                return True
    return False


def parse_alert(alert_elem) -> dict:
    """Extract fields from a CAP <alert> element into a MongoDB document."""
    area_descs = [
        a.findtext(f"{{{NS_CAP}}}areaDesc", "").strip()
        for a in alert_elem.findall(f".//{{{NS_CAP}}}area")
    ]
    return {
        "alert_id":    _text(alert_elem, "identifier"),
        "event":       _info_text(alert_elem, "event"),
        "headline":    _info_text(alert_elem, "headline"),
        "description": _info_text(alert_elem, "description"),
        "severity":    _info_text(alert_elem, "severity"),
        "area_desc":   "; ".join(filter(None, area_descs)),
        "sender":      _text(alert_elem, "sender"),
        "sent":        _text(alert_elem, "sent"),
        "status":      _text(alert_elem, "status"),
        "msg_type":    _text(alert_elem, "msgType"),
        "source":      "IPAWS",
    }


def fetch_and_store(col, since_dt: datetime) -> int:
    """Fetch IPAWS alerts since `since_dt`, store new Florida ones. Returns count stored."""
    since_str = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{IPAWS_BASE_URL}/{since_str}"

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return 0

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error(f"XML parse error: {e}")
        return 0

    # Root is <ns1:alerts>; alert elements are direct children
    alert_elems = [
        child for child in root
        if child.tag == f"{{{NS_CAP}}}alert" or child.tag == "alert"
    ]

    stored = 0
    for alert_elem in alert_elems:
        if not is_florida(alert_elem):
            continue

        data = parse_alert(alert_elem)
        if not data["alert_id"]:
            continue

        result = col.update_one(
            {"alert_id": data["alert_id"]},
            {
                "$setOnInsert": {
                    **data,
                    "tts_generated": False,
                    "fetched_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        if result.upserted_id:
            logger.info(f"New IPAWS alert: [{data['event']}] {data['headline'][:80]}")
            stored += 1

    return stored


def main():
    client = MongoClient(MONGO_URI)
    col    = client["weather_rss"]["nws_alerts"]
    logger.info(
        f"IPAWS fetcher started — poll={POLL_INTERVAL}s, "
        f"feed={IPAWS_BASE_URL}"
    )

    # On first run, look back 5 minutes to catch any recent alerts
    since_dt = datetime.now(timezone.utc) - timedelta(minutes=5)

    while True:
        now    = datetime.now(timezone.utc)
        stored = fetch_and_store(col, since_dt)
        since_dt = now  # advance the window to now

        if stored:
            logger.info(f"Stored {stored} new Florida IPAWS alert(s).")
        else:
            logger.debug("No new Florida IPAWS alerts.")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
