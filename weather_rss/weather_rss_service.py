import os
import logging
from email_utils import send_success_email
import feedparser

LOG_FILE = "/home/lh_admin/weather_rss/logs/weather.log"
logging.basicConfig(filename=LOG_FILE,
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s:%(message)s')

FEED_URL = "https://w1.weather.gov/xml/current_obs/KGNV.rss"

def process_weather_rss():
    try:
        feed = feedparser.parse(FEED_URL)
        logging.info(f"Fetched {len(feed.entries)} entries from feed.")

        # Send success email after processing
        send_success_email()
    except Exception as e:
        logging.error(f"Error processing RSS feed: {e}")

if __name__ == "__main__":
    process_weather_rss()
