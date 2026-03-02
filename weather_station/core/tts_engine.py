# services/tts_engine.py
import logging
import pyttsx3

class TTSEngine:
    def __init__(self, settings):
        self.settings = settings
        self.engine = pyttsx3.init()
        logging.info("TTSEngine initialized.")

    def text_to_wav(self, text, output_path):
        """Generate a WAV file from text."""
        try:
            logging.info(f"Generating TTS WAV: {output_path}")
            self.engine.save_to_file(text, output_path)
            self.engine.runAndWait()
        except Exception as e:
            logging.error(f"TTS generation failed: {e}")
