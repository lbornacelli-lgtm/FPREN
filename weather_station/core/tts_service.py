import logging
import os
import subprocess
import tempfile
import time
from gtts import gTTS

GTTS_LANG    = os.getenv("GTTS_LANG",    "en")
GTTS_TLD     = os.getenv("GTTS_TLD",     "com")
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE", "plughw:0,3")
GTTS_DELAY   = float(os.getenv("GTTS_DELAY", "1.5"))  # seconds between requests
GTTS_429_DELAYS = [30, 60, 120]  # backoff delays (seconds) on rate-limit responses
logger = logging.getLogger(__name__)

class TTSService:
    def __init__(self, lang=GTTS_LANG, tld=GTTS_TLD):
        self.lang = lang
        self.tld  = tld
        logger.info("TTSService initialized (engine: gTTS, delay=%.1fs)", GTTS_DELAY)

    def _synthesise(self, text, output_path):
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        tmp = output_path + ".tmp.mp3"
        for attempt, backoff in enumerate([0] + GTTS_429_DELAYS):
            if backoff:
                logger.warning("gTTS rate-limited (429) — waiting %ds before retry %d/%d",
                               backoff, attempt, len(GTTS_429_DELAYS))
                time.sleep(backoff)
            try:
                gTTS(text=text, lang=self.lang, tld=self.tld).save(tmp)
                os.replace(tmp, output_path)
                time.sleep(GTTS_DELAY)
                return
            except Exception as exc:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                if "429" not in str(exc) or attempt == len(GTTS_429_DELAYS):
                    raise
                # 429 — fall through to next iteration for backoff retry

    def say(self, text, output_file=None):
        if not text or not text.strip():
            logger.warning("TTSService.say() called with empty text.")
            return None
        try:
            if output_file:
                self._synthesise(text, output_file)
                logger.info("TTS saved: %s", output_file)
                return output_file
            else:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    tmp_path = f.name
                try:
                    self._synthesise(text, tmp_path)
                    subprocess.run(["aplay", "-D", AUDIO_DEVICE, tmp_path],
                                   check=True, timeout=30)
                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
        except Exception as e:
            logger.error("TTS error: %s", e)
            raise
