# services/alert_service.py
import logging
import time

from services.mongo_service import MongoService
from services.tts_engine import TTSEngine
from services.file_router import FileRouter
from services.wav_cleanup import run_cleanup
from core.alert_processor import process_alerts

logger = logging.getLogger("AlertService")

ALERT_INTERVAL   = 30       # seconds between TTS conversion runs
CLEANUP_INTERVAL = 86400    # seconds between cleanup/email runs (24 h)


class AlertService:
    """
    Background service that:
      1. Converts new NWS alerts to WAV files every ALERT_INTERVAL seconds.
      2. Runs WAV cleanup (delete files older than 3 days) once per day.
    """

    def __init__(self, settings):
        self.mongo  = MongoService()
        self.tts    = TTSEngine(settings)
        self.router = FileRouter(settings)
        self._last_cleanup = 0.0

    def run(self):
        logger.info("AlertService started (alert interval=%ds, cleanup interval=%dh).",
                    ALERT_INTERVAL, CLEANUP_INTERVAL // 3600)

        while True:
            # --- Alert processing ---
            try:
                process_alerts(self.mongo, self.tts, self.router)
            except Exception as e:
                logger.error(f"Alert processing error: {e}")

            # --- Daily cleanup ---
            now = time.monotonic()
            if now - self._last_cleanup >= CLEANUP_INTERVAL:
                try:
                    run_cleanup()
                except Exception as e:
                    logger.error(f"Cleanup error: {e}")
                self._last_cleanup = now

            time.sleep(ALERT_INTERVAL)
