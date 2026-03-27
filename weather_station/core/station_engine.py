import logging
import time
import signal

from core.audio_engine import AudioEngine
from core.weather_processor import WeatherProcessor
from services.watchdog import update_heartbeat


class StationEngine:
    """Main loop for the FPREN weather station.

    Fetches and processes weather data on a configurable interval,
    updates the watchdog heartbeat, and handles graceful shutdown
    via SIGINT / SIGTERM.
    """

    def __init__(self, settings):
        self.logger = logging.getLogger(__name__)
        self.settings = settings
        self.audio_engine = AudioEngine(settings)
        self.weather_processor = WeatherProcessor(settings, self.audio_engine)
        self.running = False
        self._consecutive_errors = 0
        self._max_consecutive_errors = getattr(settings, "MAX_CONSECUTIVE_ERRORS", 10)
        self.logger.info("StationEngine initialized.")

    def _register_signals(self):
        """Register SIGINT and SIGTERM for graceful shutdown."""
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        self.logger.info("Shutdown signal received (%s) — stopping station.", signum)
        self.running = False

    def start_station(self):
        """Start the main station loop."""
        self._register_signals()
        self.running = True
        interval = getattr(self.settings, "FETCH_INTERVAL_SECONDS", 60)
        self.logger.info("StationEngine loop starting (interval: %ds).", interval)

        while self.running:
            try:
                update_heartbeat(self.settings.WATCHDOG_PATH)
                self.weather_processor.fetch_and_process()
                self._consecutive_errors = 0

            except Exception as e:
                self._consecutive_errors += 1
                self.logger.error(
                    "Error in main loop (%d/%d): %s",
                    self._consecutive_errors,
                    self._max_consecutive_errors,
                    e,
                    exc_info=True,
                )
                if self._consecutive_errors >= self._max_consecutive_errors:
                    self.logger.critical(
                        "Too many consecutive errors — shutting down station."
                    )
                    self.running = False
                    break

            time.sleep(interval)

        self.logger.info("StationEngine stopped.")

    def stop_station(self):
        """Programmatically stop the station loop."""
        self.logger.info("stop_station() called.")
        self.running = False
