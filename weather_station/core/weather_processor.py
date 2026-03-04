import itertools
import logging
from datetime import datetime

# Rotation order for regular (non-alert, non-top-of-hour) slots.
# Categories are tried in order; any category with no WAV files is skipped.
# Both station audio dirs and shared playlist dirs are included so content
# from either location is picked up automatically.
_ROTATION = [
    "educational",
    "imaging",
    "weather",
    "traffic",
    "generated_wav_files",
    "pl_educational",
    "pl_imaging",
    "pl_weather",
    "pl_traffic",
    "pl_generated_wav_files",
]


class WeatherProcessor:
    def __init__(self, settings, audio_engine):
        self.logger = logging.getLogger("WeatherProcessor")
        self.settings = settings
        self.audio_engine = audio_engine
        self._rotation = itertools.cycle(_ROTATION)
        self.logger.info("WeatherProcessor initialized")

    def _play_next_available(self):
        """Try each category in rotation until one has files, then play it."""
        for _ in range(len(_ROTATION)):
            category = next(self._rotation)
            if self.audio_engine.file_router.get_next_file(category) is not None:
                self.logger.info(f"Playing category: {category}")
                self.audio_engine.play_next(category)
                return
        self.logger.warning("No audio files found in any category")

    def fetch_and_process(self):
        """
        Decide which audio to play:
        - Pending alert WAVs override everything (priority_1 first)
        - Top-of-hour content at :00
        - Otherwise rotate through all available content categories
        """
        try:
            next_alert = self.audio_engine.file_router.get_next_alert_file()

            if next_alert:
                self.logger.info(f"Alert detected — broadcasting: {next_alert}")
                self.audio_engine.play_alert(next_alert)
            elif datetime.now().minute == 0:
                self.logger.info("Top of hour — playing top_of_hour content")
                # Try station top_of_hour first, fall back to playlist version
                if self.audio_engine.file_router.get_next_file("top_of_hour") is not None:
                    self.audio_engine.play_next("top_of_hour")
                elif self.audio_engine.file_router.get_next_file("pl_top_of_hour") is not None:
                    self.audio_engine.play_next("pl_top_of_hour")
                else:
                    self._play_next_available()
            else:
                self._play_next_available()

        except Exception as e:
            self.logger.error(f"Error in fetch_and_process: {e}")
