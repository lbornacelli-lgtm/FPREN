#!/usr/bin/env python3
import logging, os, signal, sys, time
from pymongo import MongoClient
from weather_station.services.ipaws_fetcher import run_once as nws_run
from weather_station.services.county_rss_fetcher import run_once as county_run
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "weather_rss"
NWS_INTERVAL = int(os.getenv("IPAWS_INTERVAL", 60))
COUNTY_INTERVAL = int(os.getenv("COUNTY_INTERVAL", 120))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s [fetcher] %(message)s")
logger = logging.getLogger("alert_fetcher_runner")
def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    running = True
    def _shutdown(signum, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    last_nws = 0.0
    last_county = 0.0
    while running:
        now = time.monotonic()
        if now - last_nws >= NWS_INTERVAL:
            try:
                n = nws_run(db)
                if n: logger.info("NWS: %d new/updated", n)
            except Exception as e: logger.error("NWS error: %s", e)
            last_nws = time.monotonic()
        if now - last_county >= COUNTY_INTERVAL:
            try:
                n = county_run(db)
                if n: logger.info("County: %d new/updated", n)
            except Exception as e: logger.error("County error: %s", e)
            last_county = time.monotonic()
        time.sleep(5)
    client.close()
if __name__ == "__main__":
    main()
