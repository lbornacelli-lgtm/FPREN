#!/usr/bin/env python3
"""
county_rss_fetcher.py
---------------------
Fetches NWS alerts by FL county using NWS forecast zone codes (FLZ*).

Each county maps to one or more NWS forecast zone IDs. The fetcher queries
`api.weather.gov/alerts/active?zone=<FLZ_CODES>` per county and upserts
results into the nws_alerts collection, tagging them with
`source: "county_nws:<county_slug>"` so the County Alerts dashboard tab can
surface county-specific results.

NOTE: NWS issues most FL alerts against forecast zones (FLZ prefix), not
county FIPS codes (FLC prefix). Using FLC codes returns empty results.
"""
import logging, os, urllib3
from datetime import datetime, timezone
import requests
from pymongo import MongoClient
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("county_rss_fetcher")

MONGO_URI        = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME          = "weather_rss"
COLLECTION       = "nws_alerts"
REQUEST_TIMEOUT  = int(os.getenv("COUNTY_RSS_TIMEOUT", 20))
NWS_ZONE_URL     = "https://api.weather.gov/alerts/active?zone={zones}"

# (county_name, comma_separated_FLZ_zone_codes)
# Zone codes obtained from api.weather.gov/zones?area=FL&type=forecast
FLORIDA_COUNTIES = [
    ("Alachua",       "FLZ136,FLZ236"),
    ("Baker",         "FLZ023"),
    ("Bay",           "FLZ012,FLZ112"),
    ("Bradford",      "FLZ031"),
    ("Brevard",       "FLZ247,FLZ347,FLZ447,FLZ547,FLZ647,FLZ747"),
    ("Broward",       "FLZ071,FLZ072,FLZ172"),
    ("Calhoun",       "FLZ013"),
    ("Charlotte",     "FLZ162,FLZ262"),
    ("Citrus",        "FLZ142,FLZ242"),
    ("Clay",          "FLZ132,FLZ232"),
    ("Collier",       "FLZ069,FLZ070"),
    ("Columbia",      "FLZ322,FLZ422,FLZ522"),
    ("DeSoto",        "FLZ061"),
    ("Dixie",         "FLZ034,FLZ134"),
    ("Duval",         "FLZ125,FLZ325,FLZ425"),
    ("Escambia",      "FLZ201,FLZ202"),
    ("Flagler",       "FLZ038,FLZ138"),
    ("Franklin",      "FLZ015,FLZ115"),
    ("Gadsden",       "FLZ016"),
    ("Gilchrist",     "FLZ035"),
    ("Glades",        "FLZ063"),
    ("Gulf",          "FLZ014,FLZ114"),
    ("Hamilton",      "FLZ120,FLZ220"),
    ("Hardee",        "FLZ056"),
    ("Hendry",        "FLZ066"),
    ("Hernando",      "FLZ148,FLZ248"),
    ("Highlands",     "FLZ057"),
    ("Hillsborough",  "FLZ151,FLZ251"),
    ("Holmes",        "FLZ009"),
    ("Indian River",  "FLZ154,FLZ254"),
    ("Jackson",       "FLZ011"),
    ("Jefferson",     "FLZ018,FLZ118"),
    ("Lafayette",     "FLZ029"),
    ("Lake",          "FLZ044,FLZ144"),
    ("Lee",           "FLZ165,FLZ265"),
    ("Leon",          "FLZ017"),
    ("Levy",          "FLZ139,FLZ239"),
    ("Liberty",       "FLZ326,FLZ426"),
    ("Madison",       "FLZ019"),
    ("Manatee",       "FLZ155,FLZ255"),
    ("Marion",        "FLZ140,FLZ240,FLZ340"),
    ("Martin",        "FLZ164,FLZ264"),
    ("Miami-Dade",    "FLZ073,FLZ074,FLZ173,FLZ174"),
    ("Monroe",        "FLZ075,FLZ076,FLZ077,FLZ078"),
    ("Nassau",        "FLZ024,FLZ124"),
    ("Okaloosa",      "FLZ205,FLZ206"),
    ("Okeechobee",    "FLZ058"),
    ("Orange",        "FLZ045"),
    ("Osceola",       "FLZ053"),
    ("Palm Beach",    "FLZ067,FLZ068,FLZ168"),
    ("Pasco",         "FLZ149,FLZ249"),
    ("Pinellas",      "FLZ050"),
    ("Polk",          "FLZ052"),
    ("Putnam",        "FLZ137,FLZ237"),
    ("St. Johns",     "FLZ233,FLZ333,FLZ433,FLZ533,FLZ633"),
    ("St. Lucie",     "FLZ159,FLZ259"),
    ("Santa Rosa",    "FLZ203,FLZ204"),
    ("Sarasota",      "FLZ160,FLZ260"),
    ("Seminole",      "FLZ046"),
    ("Sumter",        "FLZ043"),
    ("Suwannee",      "FLZ021"),
    ("Taylor",        "FLZ028,FLZ128"),
    ("Union",         "FLZ030"),
    ("Volusia",       "FLZ041,FLZ141"),
    ("Wakulla",       "FLZ027,FLZ127"),
    ("Walton",        "FLZ007,FLZ008,FLZ108"),
    ("Washington",    "FLZ010"),
]


def _parse_feature(feature, county):
    p = feature.get("properties", {})
    if p.get("status", "").lower() != "actual":
        return None
    alert_id = p.get("id", "")
    if not alert_id:
        return None
    return {
        "alert_id":   alert_id,
        "event":      p.get("event", ""),
        "headline":   p.get("headline", "") or p.get("event", ""),
        "description": p.get("description", "") or "",
        "severity":   p.get("severity", "Unknown"),
        "urgency":    p.get("urgency", "Unknown"),
        "certainty":  p.get("certainty", "Unknown"),
        "area_desc":  p.get("areaDesc", ""),
        "sender":     p.get("senderName", "NWS"),
        "sent":       p.get("sent", ""),
        "expires":    p.get("expires", ""),
        "source":     "county_nws:" + county.lower().replace(" ", "_").replace(".", ""),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "tts_generated": False,
    }


def _fetch_county(county, zones):
    url = NWS_ZONE_URL.format(zones=zones)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, verify=False,
                            headers={"User-Agent": "FPREN-WeatherStation/1.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("%s: fetch error: %s", county, e)
        return []
    docs = []
    for feature in data.get("features", []):
        doc = _parse_feature(feature, county)
        if doc:
            docs.append(doc)
    if docs:
        logger.info("%s: %d active alert(s)", county, len(docs))
    return docs


def run_once(db):
    col = db[COLLECTION]
    upserted = 0
    for county, zones in FLORIDA_COUNTIES:
        for doc in _fetch_county(county, zones):
            alert_id = doc["alert_id"]
            # Update all fields except tts_generated (preserve existing TTS state).
            # setOnInsert ensures tts_generated=False only on brand-new docs.
            update_fields = {k: v for k, v in doc.items()
                             if k not in ("tts_generated", "fetched_at")}
            result = col.update_one(
                {"alert_id": alert_id},
                {
                    "$set":         update_fields,
                    "$setOnInsert": {"tts_generated": False,
                                     "fetched_at":    doc["fetched_at"]},
                },
                upsert=True,
            )
            if result.upserted_id or result.modified_count:
                logger.info("%s new/updated: %s", county, doc["event"])
                upserted += 1
    return upserted


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(message)s")
    client = MongoClient(MONGO_URI)
    db     = client[DB_NAME]
    db[COLLECTION].create_index("alert_id", unique=True, sparse=True)
    n = run_once(db)
    logger.info("County fetch complete -- %d new/updated alerts", n)
    client.close()


if __name__ == "__main__":
    main()
