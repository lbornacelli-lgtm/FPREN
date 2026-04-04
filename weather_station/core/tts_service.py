#!/usr/bin/env python3
# weather_station/core/tts_service.py
"""
tts_service.py
--------------
Primary TTS engine using Piper (local, free, no rate limits).
ElevenLabs remains available via elevenlabs_tts.py for high-severity alerts.
"""

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

PIPER_BIN   = os.getenv("PIPER_BIN", "/home/ufuser/Fpren-main/venv/bin/piper")
VOICE_MODEL = os.getenv("PIPER_VOICE_MODEL",
              "/home/ufuser/Fpren-main/weather_station/voices/en_US-amy-medium.onnx")
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE", "plughw:0,3")


class TTSService:
    def __init__(self, voice_model=None):
        self.voice_model = voice_model or VOICE_MODEL
        logger.info("TTSService initialized (engine: Piper, model: %s)",
                    os.path.basename(self.voice_model))

    def _synthesise(self, text: str, output_path: str):
        """Run Piper to generate WAV, then convert to MP3."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        tmp_wav = output_path + ".tmp.wav"
        tmp_mp3 = output_path + ".tmp.mp3"
        try:
            # Piper: stdin → WAV
            result = subprocess.run(
                [PIPER_BIN, "--model", self.voice_model,
                 "--output_file", tmp_wav],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Piper failed: {result.stderr.decode()}")

            # Convert WAV → MP3 via ffmpeg
            conv = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_wav, "-q:a", "4", tmp_mp3],
                capture_output=True, timeout=30,
            )
            if conv.returncode != 0:
                # ffmpeg conversion failed — raise so the caller logs the failure
                # and does NOT record a bad path in MongoDB. The WAV temp file
                # is cleaned up in the finally block below.
                raise RuntimeError(
                    f"ffmpeg WAV→MP3 conversion failed (exit {conv.returncode}): "
                    f"{conv.stderr.decode(errors='replace').strip()}"
                )

            os.replace(tmp_mp3, output_path)
        finally:
            for f in (tmp_wav, tmp_mp3):
                if os.path.exists(f):
                    try:
                        os.unlink(f)
                    except OSError:
                        pass

    def say(self, text: str, output_file: str = None) -> str | None:
        if not text or not text.strip():
            logger.warning("TTSService.say() called with empty text.")
            return None
        try:
            if output_file:
                self._synthesise(text, output_file)
                logger.info("TTS saved: %s", output_file)
                return output_file
            else:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    tmp_path = f.name
                try:
                    result = subprocess.run(
                        [PIPER_BIN, "--model", self.voice_model,
                         "--output_file", tmp_path],
                        input=text.encode("utf-8"),
                        capture_output=True, timeout=30,
                    )
                    if result.returncode == 0:
                        subprocess.run(
                            ["aplay", "-D", AUDIO_DEVICE, tmp_path],
                            check=True, timeout=30,
                        )
                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
        except Exception as e:
            logger.error("TTS error: %s", e)
            raise
