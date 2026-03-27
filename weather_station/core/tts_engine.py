"""
tts_engine.py

Thin backward-compatible wrapper around TTSService.
Existing code that imports TTSEngine continues to work unchanged.
All actual TTS logic lives in tts_service.py.
"""

import logging
from weather_station.core.tts_service import TTSService

logger = logging.getLogger(__name__)


class TTSEngine(TTSService):
    """Backward-compatible alias for TTSService.

    Prefer importing TTSService directly in new code.
    """

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        logger.info("TTSEngine ready (via TTSService → LiteLLM → ElevenLabs).")

    def text_to_wav(self, text: str, output_path: str) -> str | None:
        """Legacy method name — delegates to say()."""
        # Note: output is now MP3 despite the method name.
        # Rename output_path extension if needed by callers.
        return self.say(text, output_file=output_path)
