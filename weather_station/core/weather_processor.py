import logging
from datetime import datetime

class WeatherProcessor:
    def __init__(self, settings, audio_engine):
        self.logger = logging.getLogger("WeatherProcessor")
        self.settings = settings
        self.audio_engine = audio_engine
        self.logger.info("WeatherProcessor initialized")

    def fetch_and_process(self):
        """
        Decide which audio to play:
        - Alerts override everything
        - Top-of-hour content
        - Educational audio otherwise
        """
        try:
            alert_active = False  # Replace with real alert logic
            top_of_hour = datetime.now().minute == 0  # top of the hour

            if alert_active:
                category = "alerts"
                # Example: TTS alert
                self.audio_engine.play_tts("Severe weather alert in your area!")
            elif top_of_hour:
                category = "top_of_hour"
            else:
                category = "educational"

            if not alert_active:  # normal audio playback
                self.logger.info(f"Fetching and processing {category} audio...")
                self.audio_engine.play_next(category)

        except Exception as e:
            self.logger.error(f"Error in fetch_and_process: {e}")
