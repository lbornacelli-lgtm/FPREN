import logging
import time
from core.audio_engine import AudioEngine
from core.weather_processor import WeatherProcessor
from services.watchdog import update_heartbeat

class StationEngine:
    def __init__(self, settings):
        self.logger = logging.getLogger("StationEngine")
        self.settings = settings

        self.audio_engine = AudioEngine(settings)
        self.weather_processor = WeatherProcessor(settings, self.audio_engine)
        self.running = True
        self.logger.info("StationEngine initialized")

    def start_station(self):
        self.logger.info("Starting StationEngine loop...")
        interval = self.settings.FETCH_INTERVAL_SECONDS

        while self.running:
            try:
                update_heartbeat(self.settings.WATCHDOG_PATH)
                self.weather_processor.fetch_and_process()
            except Exception as e:
                self.logger.error(f"Error in main loop: {e}")
            time.sleep(interval)
