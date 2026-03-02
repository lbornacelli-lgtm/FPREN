import wave
import logging
import os
from piper import PiperVoice

VOICE_MODEL = os.getenv(
    "PIPER_VOICE_MODEL",
    "/home/lh_admin/weather_station/voices/en_US-amy-medium.onnx",
)


class TTSEngine:
    def __init__(self, settings):
        self.logger = logging.getLogger("TTSEngine")
        self.settings = settings
        self.voice = PiperVoice.load(VOICE_MODEL)
        self.logger.info(f"TTSEngine initialized (Piper: {os.path.basename(VOICE_MODEL)})")

    def _write_wav(self, text, path):
        """Synthesize text to a WAV file using Piper's AudioChunk iterator."""
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.voice.config.sample_rate)
            for chunk in self.voice.synthesize(text):
                wf.writeframes(chunk.audio_int16_bytes)

    def say(self, text, output_file=None):
        """Convert text to speech using Piper and save to WAV if output_file is given."""
        try:
            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                self._write_wav(text, output_file)
                self.logger.info(f"TTS generated: {output_file}")
                return output_file
            else:
                import subprocess, tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name
                self._write_wav(text, tmp_path)
                subprocess.run(["aplay", "-D", "plughw:0,3", tmp_path], check=True)
                os.unlink(tmp_path)
        except Exception as e:
            self.logger.error(f"TTS error: {e}")
