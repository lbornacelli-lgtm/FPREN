#!/usr/bin/env python3
# weather_station/services/county_rss_fetcher.py
import logging
import os
import urllib3
from datetime import datetime, timezone
import requests
from pymongo import MongoClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("county_rss_fetcher")

MONGO_URI       = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME         = "weather_rss"
COLLECTION      = "nws_alerts"
REQUEST_TIMEOUT = int(os.getenv("COUNTY_RSS_TIMEOUT", 20))
NWS_ZONE_URL    = "https://api.weather.gov/alerts/active?zone={zone}"

# Florida county FIPS codes — FLC + zero-padded FIPS
FLORIDA_COUNTIES = [
    ("Alachua",     "FLC001"), ("Baker",       "FLC003"), ("Bay",         "FLC005"),
    ("Bradford",    "FLC007"), ("Brevard",     "FLC009"), ("Broward",     "FLC011"),
    ("Calhoun",     "FLC013"), ("Charlotte",   "FLC015"), ("Citrus",      "FLC017"),
    ("Clay",        "FLC019"), ("Collier",     "FLC021"), ("Columbia",    "FLC023"),
    ("Miami-Dade",  "FLC025"), ("DeSoto",      "FLC027"), ("Dixie",       "FLC029"),
    ("Duval",       "FLC031"), ("Escambia",    "FLC033"), ("Flagler",     "FLC035"),
    ("Franklin",    "FLC037"), ("Gadsden",     "FLC039"), ("Gilchrist",   "FLC041"),
    ("Glades",      "FLC043"), ("Gulf",        "FLC045"), ("Hamilton",    "FLC047"),
    ("Hardee",      "FLC049"), ("Hendry",      "FLC051"), ("Hernando",    "FLC053"),
    ("Highlands",   "FLC055"), ("Hillsborough","FLC057"), ("Holmes",      "FLC059"),
    ("Indian River","FLC061"), ("Jackson",     "FLC063"), ("Jefferson",   "FLC065"),
    ("Lafayette",   "FLC067"), ("Lake",        "FLC069"), ("Lee",         "FLC071"),
    ("Leon",        "FLC073"), ("Levy",        "FLC075"), ("Liberty",     "FLC077"),
    ("Madison",     "FLC079"), ("Manatee",     "FLC081"), ("Marion",      "FLC083"),
    ("Martin",      "FLC085"), ("Monroe",      "FLC087"), ("Nassau",      "FLC089"),
    ("Okaloosa",    "FLC091"), ("Okeechobee",  "FLC093"), ("Orange",      "FLC095"),
    ("Osceola",     "FLC097"), ("Palm Beach",  "FLC099"), ("Pasco",       "FLC101"),
    ("Pinellas",    "FLC103"), ("Polk",        "FLC105"), ("Putnam",      "FLC107"),
    ("St. Johns",   "FLC109"), ("St. Lucie",   "FLC111"), ("Santa Rosa",  "FLC113"),
    ("Sarasota",    "FLC115"), ("Seminole",    "FLC117"), ("Sumter",      "FLC119"),
    ("Suwannee",    "FLC121"), ("Taylor",      "FLC123"), ("Union",       "FLC125"),
    ("Volusia",     "FLC127"), ("Wakulla",     "FLC129"), ("Walton",      "FLC131"),
    ("Washington",  "FLC133"),
]

def _parse_feature(feature: dict, county: str) -> dict | None:
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
        "source":        f"county_nws:{county.lower().replace(' ', '_').replace('.', '')}",
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
        "tts_generated": False,
    }

def _fetch_county(county: str, zone: str) -> list:
    url = NWS_ZONE_URL.format(zone=zone)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False,
                            headers={"User-Agent": "FPREN-WeatherStation/1.0",
                                     "Accept": "application/geo+json"})
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning("%s: fetch error: %s", county, e)
        return []
    except Exception as e:
        logger.warning("%s: parse error: %s", county, e)
        return []
    docs = []
    for feature in data.get("features", []):
        doc = _parse_feature(feature, county)
        if doc:
            docs.append(doc)
    if docs:
        logger.info("%s: %d active alert(s)", county, len(docs))
    else:
        logger.debug("%s: no active alerts", county)
    return docs

def run_once(db) -> int:
    col      = db[COLLECTION]
    upserted = 0
    for county, zone in FLORIDA_COUNTIES:
        docs = _fetch_county(county, zone)
        for doc in docs:
            alert_id = doc["alert_id"]
            existing = col.find_one({"alert_id": alert_id})
            if existing:
                changed = any(existing.get(k) != doc[k]
                              for k in ("event","headline","description","area_desc","severity"))
                if changed:
                    col.update_one({"alert_id": alert_id},
                                   {"$set": {**doc, "tts_generated": False}})
                    logger.info("Updated [%s]: %s", county, doc["event"])
                    upserted += 1
            else:
                col.insert_one(doc)
                logger.info("New [%s]: %s", county, doc["event"])
                upserted += 1
    return upserted

def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s [county_rss_fetcher] %(message)s")
    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]
    db[COLLECTION].create_index("alert_id", unique=True, sparse=True)
    n = run_once(db)
    logger.info("County fetch complete -- %d new/updated alerts", n)
    client.close()

if __name__ == "__main__":
    main()
