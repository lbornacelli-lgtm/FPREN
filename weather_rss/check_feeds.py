#!/usr/bin/env python3
import requests
from datetime import datetime
from pathlib import Path
import logging
import subprocess
from pymongo import MongoClient

# ------------------ Configuration ------------------
FEEDS_DIR = Path("/home/lh_admin/feeds")
FEEDS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = FEEDS_DIR / "weather_rss.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


# MongoDB configuration
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "weather_rss"
FEEDS_COLLECTION = "feeds"         # stores feed info
STATUS_COLLECTION = "feed_status"  # stores last fetch/status

# Setup logging
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Connect to MongoDB
client = MongoClient("mongodb://localhost:27017/")
db = client["weather"]
feeds = db["feeds"]
status_col = db[STATUS_COLLECTION]

for f in feeds.find({}, {"title":1, "priority":1}):
    print(f)

# ------------------ Functions ------------------
def prepare_feed(feed):
    """Ensure each feed has a filename for saving XML."""
    if "filename" not in feed:
        safe_name = feed["name"].lower().replace(" ", "_").replace("/", "_")
        feed["filename"] = f"{safe_name}.xml"
    return feed

def fetch_feed(feed):
    filename = FEEDS_DIR / feed["filename"]
    now = datetime.utcnow()

    try:
        response = requests.get(feed["url"], timeout=15)
        response.raise_for_status()
        filename.write_bytes(response.content)
        size_kb = round(len(response.content) / 1024, 2)

        # Update status collection
        status_col.update_one(
            {"filename": feed["filename"]},
            {"$set": {
                "feed_name": feed["name"],
                "filename": feed["filename"],
                "url": feed["url"],
                "last_fetch": now,
                "last_success": now,
                "last_failure": None,
                "status": "OK",
                "error_message": None,
                "file_size_kb": size_kb
            }, "$inc": {"update_count": 1}},
            upsert=True
        )

        logging.info(f"✅ {feed['name']} fetched successfully ({size_kb} KB)")
        return True, None

    except Exception as e:
        # Update status collection on failure
        status_col.update_one(
            {"filename": feed["filename"]},
            {"$set": {
                "feed_name": feed["name"],
                "filename": feed["filename"],
                "url": feed["url"],
                "last_fetch": now,
                "last_failure": now,
                "status": "ERROR",
                "error_message": str(e)
            }, "$inc": {"update_count": 1}},
            upsert=True
        )

        logging.error(f"❌ {feed['name']} failed: {e}")
        return False, str(e)

# ------------------ Main Script ------------------
def main():
    results = []
    failing_feeds = []

    feeds = list(feeds_col.find())
    if not feeds:
        print("No feeds found in MongoDB. Insert feeds into the 'feeds' collection first.")
        return

    print("Checking RSS feeds...\n")
    for feed in feeds:
        feed = prepare_feed(feed)
        ok, error = fetch_feed(feed)
        if ok:
            print(f"✅ {feed['name']} is working")
            results.append(f"{feed['name']} : OK")
        else:
            print(f"❌ {feed['name']} failed: {error}")
            results.append(f"{feed['name']} : ERROR - {error}")
            failing_feeds.append(f"{feed['name']} : ERROR - {error}")

    # Save full report
    report_file = FEEDS_DIR / "feed_status_report.txt"
    report_file.write_text("\n".join(results))
    print(f"\nFull report saved to {report_file}")

    # Copy failing feeds to clipboard
    if failing_feeds:
        try:
            subprocess.run("xclip -selection clipboard", input="\n".join(failing_feeds),
                           text=True, shell=True)
            print("✅ Failing feeds copied to clipboard. You can now paste them with Ctrl+V.")
        except Exception as e:
            print(f"⚠️ Could not copy to clipboard: {e}")
    else:
        print("✅ All feeds are working. Nothing copied to clipboard.")

if __name__ == "__main__":
    main()
