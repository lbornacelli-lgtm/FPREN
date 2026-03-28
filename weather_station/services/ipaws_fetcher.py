#!/usr/bin/env python3
# weather_station/services/ipaws_fetcher.py
import logging
import os
import urllib3
from datetime import datetime, timezone
import requests
from pymongo import MongoClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("ipaws_fetcher")

MONGO_URI       = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME         = "weather_rss"
COLLECTION      = "nws_alerts"
NWS_FL_URL      = "https://api.weather.gov/alerts/active?area=FL"
REQUEST_TIMEOUT = int(os.getenv("IPAWS_TIMEOUT", 30))

def _parse_feature(feature: dict) -> dict | None:
    p = feature.get("properties", {})
    if p.get("status", "").lower() != "actual":
        return None
    alert_id = p.get("id", "")
    if not alert_id:
        return None
    return {
        "alert_id":      alert_id,
        "event":         p.get("event", ""),
        "headline":      p.get("headline", "") or p.get("event", ""),
        "description":   p.get("description", "") or "",
        "severity":      p.get("severity", "Unknown"),
        "urgency":       p.get("urgency", "Unknown"),
        "certainty":     p.get("certainty", "Unknown"),
        "area_desc":     p.get("areaDesc", ""),
        "sender":        p.get("senderName", "NWS"),
        "sent":          p.get("sent", ""),
        "expires":       p.get("expires", ""),
        "source":        "NWS_API",
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
        "tts_generated": False,
    }

def _fetch_and_parse() -> list:
    try:
        resp = requests.get(NWS_FL_URL, timeout=REQUEST_TIMEOUT, verify=False,
                            headers={"User-Agent": "FPREN-WeatherStation/1.0",
                                     "Accept": "application/geo+json"})
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("NWS API fetch error: %s", e)
        return []
    try:
        data = resp.json()
    except Exception as e:
        logger.error("NWS API JSON parse error: %s", e)
        return []
    alerts = []
    for feature in data.get("features", []):
        doc = _parse_feature(feature)
        if doc:
            alerts.append(doc)
    return alerts

def run_once(db) -> int:
    alerts = _fetch_and_parse()
    if not alerts:
        logger.debug("NWS API: no active Florida alerts.")
        return 0
    col      = db[COLLECTION]
    upserted = 0
    for doc in alerts:
        alert_id = doc["alert_id"]
        existing = col.find_one({"alert_id": alert_id})
        if existing:
            changed = any(existing.get(k) != doc[k]
                          for k in ("event","headline","description","area_desc","severity"))
            if changed:
                col.update_one({"alert_id": alert_id},
                               {"$set": {**doc, "tts_generated": False}})
                logger.info("NWS updated: %s", doc["event"])
                upserted += 1
        else:
            col.insert_one(doc)
            logger.info("NWS new: %s -- %s", doc["event"], doc["area_desc"][:60])
            upserted += 1
    return upserted

def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s [ipaws_fetcher] %(message)s")
    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]
    db[COLLECTION].create_index("alert_id", unique=True, sparse=True)
    n = run_once(db)
    logger.info("NWS API fetch complete -- %d new/updated alerts", n)
    client.close()

if __name__ == "__main__":
    main()
