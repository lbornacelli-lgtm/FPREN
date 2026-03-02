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

    def say(self, text, output_file=None):
        """Convert text to speech using Piper and save to WAV if output_file is given."""
        try:
            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with wave.open(output_file, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.voice.config.sample_rate)
                    self.voice.synthesize(text, wf)
                self.logger.info(f"TTS generated: {output_file}")
                return output_file
            else:
                # Play directly via aplay
                import subprocess, tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp_path = tmp.name
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.voice.config.sample_rate)
                    self.voice.synthesize(text, wf)
                subprocess.run(["aplay", "-D", "plughw:0,3", tmp_path], check=True)
                os.unlink(tmp_path)
        except Exception as e:
            self.logger.error(f"TTS error: {e}")
