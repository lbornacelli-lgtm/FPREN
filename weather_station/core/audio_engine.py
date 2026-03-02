import logging
from services.file_router import FileRouter
from processing.audio_chain import apply_audio_chain
from services.tts_engine import TTSEngine
from services.fm_transmitter import FMTransmitter

class AudioEngine:
    def __init__(self, settings):
        self.logger = logging.getLogger("AudioEngine")
        self.settings = settings

        self.file_router = FileRouter(settings)
        self.tts_engine = TTSEngine(settings)
        self.fm_transmitter = FMTransmitter(settings)

        self.logger.info("AudioEngine initialized")

    def play_next(self, category="educational"):
        next_file = self.file_router.get_next_file(category)
        if next_file:
            output_file = next_file.replace(".wav", "_processed.wav")
            apply_audio_chain(next_file, output_file)

            # Send processed audio to FM
            self.fm_transmitter.play_wav(output_file)
            self.logger.info(f"Processed and broadcasted: {output_file}")

    def play_tts(self, text):
        tts_file = "/tmp/tts_output.wav"
        self.tts_engine.say(text, tts_file)
        self.fm_transmitter.play_wav(tts_file)
