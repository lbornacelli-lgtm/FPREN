import pyttsx3
import logging
import os

class TTSEngine:
    def __init__(self, settings):
        self.logger = logging.getLogger("TTSEngine")
        self.settings = settings
        self.engine = pyttsx3.init()
        self.logger.info("TTSEngine initialized")

    def say(self, text, output_file=None):
        """
        Convert text to speech and save to WAV if output_file is provided
        """
        try:
            if output_file:
                self.engine.save_to_file(text, output_file)
                self.engine.runAndWait()
                self.logger.info(f"TTS generated: {output_file}")
                return output_file
            else:
                self.engine.say(text)
                self.engine.runAndWait()
        except Exception as e:
            self.logger.error(f"TTS error: {e}")
