from playlist_engine import run_hour
from alert_processor import process_alerts
from weather_processor import process_weather
from alert_watcher import start_watcher
from cleanup_manager import cleanup_expired_alerts
import time

def start_station():
    start_watcher()

    while True:
        process_alerts()
        process_weather()
        cleanup_expired_alerts()
        run_hour()
        time.sleep(1)
