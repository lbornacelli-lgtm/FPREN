#!/usr/bin/env python3
import requests
import feedparser
from pymongo import MongoClient
from datetime import datetime, timezone
import logging
import time

# -------------------------------
# CONFIGURATION
# -------------------------------
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "weather_rss"
RSS_FEEDS = [
    {
        "name": "NHC Atlantic Tropical Weather",
        "url": "https://www.nhc.noaa.gov/index-at.xml",
        "filename": "nhc_atlantic.xml"
    },
    {
        "name": "NHC East Pacific Tropical Weather",
        "url": "https://www.nhc.noaa.gov/index-ep.xml",
        "filename": "nhc_eastpacific.xml"
    },
]

NWS_LAT = 29.6516  # Gainesville latitude
NWS_LON = -82.3248  # Gainesville longitude

FETCH_INTERVAL = 300  # seconds

LOG_FILE = "/home/lh_admin/weather_rss/logs/service.log"

# -------------------------------
# LOGGING
# -------------------------------
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# -------------------------------
# MONGODB SETUP
# -------------------------------
mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]
rss_status_col = db.feed_status
rss_history_col = db.feed_history
nws_alerts_col = db.nws_alerts

# -------------------------------
# HELPER FUNCTIONS
# -------------------------------
def fetch_rss_feed(feed):
    now = datetime.now(timezone.utc)
    try:
        r = requests.get(feed["url"], timeout=15)
        r.raise_for_status()
        content = r.content

        # Store feed content in MongoDB
        rss_status_col.update_one(
            {"filename": feed["filename"]},
            {"$set": {
                "feed_name": feed["name"],
                "url": feed["url"],
                "last_fetch": now,
                "last_success": now,
                "status": "OK",
                "file_size_bytes": len(content),
                "error_message": None
            }, "$inc": {"update_count": 1}},
            upsert=True
        )

        rss_history_col.insert_one({
            "filename": feed["filename"],
            "timestamp": now,
            "status": "OK"
        })

        logging.info(f"Fetched RSS feed: {feed['name']} ({len(content)} bytes)")

    except Exception as e:
        logging.error(f"Failed to fetch RSS feed {feed['name']}: {e}")
        rss_status_col.update_one(
            {"filename": feed["filename"]},
            {"$set": {
                "last_fetch": now,
                "last_failure": now,
                "status": "ERROR",
                "error_message": str(e)
            }},
            upsert=True
        )
        rss_history_col.insert_one({
            "filename": feed["filename"],
            "timestamp": now,
            "status": "ERROR",
            "error": str(e)
        })

def fetch_nws_alerts(lat=NWS_LAT, lon=NWS_LON):
    now = datetime.now(timezone.utc)
    url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        alerts = data.get("features", [])

        for alert in alerts:
            properties = alert.get("properties", {})
            alert_id = properties.get("id") or properties.get("event") + str(now.timestamp())

            nws_alerts_col.update_one(
                {"alert_id": alert_id},
                {"$set": {
                    "alert_id": alert_id,
                    "event": properties.get("event"),
                    "severity": properties.get("severity"),
                    "area_desc": properties.get("areaDesc"),
                    "headline": properties.get("headline"),
                    "description": properties.get("description"),
                    "instruction": properties.get("instruction"),
                    "start": properties.get("onset"),
                    "end": properties.get("ends"),
                    "fetched_at": now
                }},
                upsert=True
            )
        logging.info(f"Fetched {len(alerts)} NWS alerts for Gainesville, FL")

    except Exception as e:
        logging.error(f"Failed to fetch NWS alerts: {e}")

# -------------------------------
# MAIN LOOP
# -------------------------------
def main():
    logging.info("Weather service started")
    while True:
        # Fetch RSS feeds
        for feed in RSS_FEEDS:
            fetch_rss_feed(feed)

        # Fetch NWS alerts
        fetch_nws_alerts()

        # Wait for next interval
        time.sleep(FETCH_INTERVAL)

if __name__ == "__main__":
    main()
