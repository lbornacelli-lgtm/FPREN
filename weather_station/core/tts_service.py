"""
tts_service.py

Central TTS service for FPREN Weather Station.
Routes all text-to-speech through LiteLLM → ElevenLabs, saving MP3 output.

Usage:
    from weather_station.core.tts_service import TTSService
    tts = TTSService()
    path = tts.say("Severe thunderstorm warning.", output_file="/tmp/alert.mp3")
"""

import logging
import os
import subprocess
import tempfile

import litellm

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_MODEL  = os.getenv("TTS_MODEL",   "elevenlabs-rachel")
AUDIO_DEVICE   = os.getenv("AUDIO_DEVICE", "plughw:0,3")

logger = logging.getLogger(__name__)


class TTSService:
    """LiteLLM-backed TTS service producing ElevenLabs MP3 output.

    All TTS in the FPREN project routes through this class.
    LiteLLM handles retries, fallbacks, and provider switching
    via config.yaml — no provider-specific code lives here.
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        logger.info("TTSService initialized (model: %s)", self.model)

    def _synthesise(self, text: str, output_path: str) -> None:
        """Call LiteLLM speech endpoint and write MP3 to output_path."""
        response = litellm.speech(
            model=self.model,
            input=text,
        )
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        # Write atomically via temp file to avoid partial writes
        tmp_path = output_path + ".tmp"
        try:
            with open(tmp_path, "wb") as f:
                f.write(response.content)
            os.replace(tmp_path, output_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def say(self, text: str, output_file: str = None) -> str | None:
        """Convert text to speech via ElevenLabs through LiteLLM.

        Args:
            text:        The text to synthesize.
            output_file: If provided, saves MP3 to this path and returns it.
                         If None, plays audio immediately via aplay.

        Returns:
            output_file path if saved, otherwise None.
        """
        if not text or not text.strip():
            logger.warning("TTSService.say() called with empty text — skipping.")
            return None

        try:
            if output_file:
                self._synthesise(text, output_file)
                logger.info("TTS saved: %s", output_file)
                return output_file
            else:
                with tempfile.NamedTemporaryFile(
                    suffix=".mp3", delete=False
                ) as tmp:
                    tmp_path = tmp.name
                try:
                    self._synthesise(text, tmp_path)
                    subprocess.run(
                        ["aplay", "-D", AUDIO_DEVICE, tmp_path],
                        check=True,
                        timeout=30,
                    )
                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

        except subprocess.TimeoutExpired:
            logger.error("aplay timed out playing TTS audio.")
        except subprocess.CalledProcessError as e:
            logger.error("aplay failed (exit %d): %s", e.returncode, e)
        except litellm.exceptions.APIError as e:
            logger.error("LiteLLM/ElevenLabs API error: %s", e)
        except Exception as e:
            logger.exception("Unexpected TTS error: %s", e)

        return None
