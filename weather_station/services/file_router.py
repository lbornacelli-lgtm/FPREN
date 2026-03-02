import os
import logging
import random

class FileRouter:
    def __init__(self, settings):
        self.logger = logging.getLogger("FileRouter")
        self.settings = settings

        self.audio_dirs = {
            "top_of_hour": os.path.join(settings.AUDIO_PATH, "top_of_hour"),
            "educational": os.path.join(settings.AUDIO_PATH, "educational"),
            "imaging": os.path.join(settings.AUDIO_PATH, "imaging"),
            "alerts": os.path.join(settings.AUDIO_PATH, "alerts"),
        }
        self.logger.info("FileRouter initialized")

    def get_next_file(self, category="educational"):
        folder = self.audio_dirs.get(category)
        if not folder or not os.path.isdir(folder):
            self.logger.warning(f"No folder for category '{category}'")
            return None

        files = [f for f in os.listdir(folder) if f.lower().endswith(".wav")]
        if not files:
            self.logger.warning(f"No WAV files in '{folder}'")
            return None

        next_file = os.path.join(folder, random.choice(files))
        self.logger.info(f"Next file selected: {next_file}")
        return next_file
