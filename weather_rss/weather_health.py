#!/usr/bin/env python3
import subprocess
from pathlib import Path
from pymongo import MongoClient
from datetime import datetime, timezone

# ==========================
# CONFIGURATION
# ==========================
WATCHDOG = "/home/lh_admin/weather_rss/weather_rss.watchdog"
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "weather"
FEEDS_COLL = "feeds"
SERVICE_NAME = "weather-rss.service"
NUM_FEEDS = 5

# ==========================
# CHECK SYSTEMD SERVICE
# ==========================
def check_service(name):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return "inactive"

# ==========================
# CHECK WATCHDOG
# ==========================
def check_watchdog(path):
    p = Path(path)
    if p.exists():
        try:
            ts = p.read_text().strip()
            return ts
        except Exception as e:
            return f"Error reading watchdog: {e}"
    else:
        return "Watchdog file missing!"

# ==========================
# CHECK LAST FEEDS
# ==========================
def check_feeds(uri, db_name, coll_name, num=5):
    client = MongoClient(uri)
    db = client[db_name]
    coll = db[coll_name]
    feeds = coll.find({}, {"title":1, "priority":1, "fetched_at":1}).sort("fetched_at",-1).limit(num)
    return list(feeds)

# ==========================
# MAIN
# ==========================
if __name__ == "__main__":
    print("=== Weather Service Health Check ===\n")

    # 1. Service status
    status = check_service(SERVICE_NAME)
    print(f"Service '{SERVICE_NAME}' status: {status}\n")

    # 2. Watchdog heartbeat
    heartbeat = check_watchdog(WATCHDOG)
    print(f"Last heartbeat: {heartbeat}\n")

    # 3. Last feeds
    print(f"Last {NUM_FEEDS} feed entries from MongoDB:")
    try:
        feeds = check_feeds(MONGO_URI, DB_NAME, FEEDS_COLL, NUM_FEEDS)
        if feeds:
            for f in feeds:
                fetched = f.get("fetched_at")
                if isinstance(fetched, datetime):
                    fetched = fetched.astimezone(timezone.utc).isoformat()
                print(f"- {f.get('title')} | priority: {f.get('priority')} | fetched_at: {fetched}")
        else:
            print("No feeds found in MongoDB.")
    except Exception as e:
        print(f"Error reading feeds: {e}")
