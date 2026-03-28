"""
audio_chain.py

Applies a professional broadcast audio chain to WAV or MP3 files.
Handles MP3 → WAV conversion automatically via pydub.
Output is always WAV. Compatible with Python 3.12 + Pedalboard 0.9.22.

Usage:
    from weather_station.processing.audio_chain import apply_audio_chain
    apply_audio_chain("/tmp/raw.wav", "/tmp/processed.wav")
    apply_audio_chain("/tmp/raw.mp3", "/tmp/processed.wav")  # MP3 input also works
"""

import logging
import os
import tempfile

import soundfile as sf
from pedalboard import (
    Pedalboard, Compressor, Delay, Gain,
    HighShelfFilter, Limiter, LowShelfFilter, Reverb,
)

logger = logging.getLogger(__name__)

# ── Audio chain settings (tuned for FM broadcast speech) ─────────────────────

CHAIN_SETTINGS = {
    "gain_db":               float(os.getenv("AUDIO_GAIN_DB",       "3.0")),
    "compressor_threshold":  float(os.getenv("AUDIO_COMP_THRESHOLD", "-20.0")),
    "compressor_ratio":      float(os.getenv("AUDIO_COMP_RATIO",     "3.0")),
    "limiter_threshold":     float(os.getenv("AUDIO_LIMITER_DB",     "-1.0")),
    "high_shelf_hz":         float(os.getenv("AUDIO_HIGH_SHELF_HZ",  "10000")),
    "high_shelf_db":         float(os.getenv("AUDIO_HIGH_SHELF_DB",  "4.0")),
    "low_shelf_hz":          float(os.getenv("AUDIO_LOW_SHELF_HZ",   "120")),
    "low_shelf_db":          float(os.getenv("AUDIO_LOW_SHELF_DB",   "3.0")),
    "reverb_room_size":      float(os.getenv("AUDIO_REVERB_ROOM",    "0.3")),
    "reverb_wet_level":      float(os.getenv("AUDIO_REVERB_WET",     "0.2")),
    "delay_seconds":         float(os.getenv("AUDIO_DELAY_SECS",     "0.25")),
    "delay_feedback":        float(os.getenv("AUDIO_DELAY_FEEDBACK", "0.2")),
    "delay_mix":             float(os.getenv("AUDIO_DELAY_MIX",      "0.15")),
}


def _load_audio(input_file: str) -> tuple:
    """Load WAV or MP3 file into a numpy array.

    Returns (audio_array, samplerate, tmp_file_to_delete_or_None).
    """
    ext = os.path.splitext(input_file)[1].lower()
    if ext == ".mp3":
        from pydub import AudioSegment
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        AudioSegment.from_mp3(input_file).export(tmp.name, format="wav")
        audio, samplerate = sf.read(tmp.name)
        return audio, samplerate, tmp.name
    else:
        audio, samplerate = sf.read(input_file)
        return audio, samplerate, None


def _build_chain(s: dict) -> Pedalboard:
    """Build and return the Pedalboard processing chain."""
    return Pedalboard([
        Gain(gain_db=s["gain_db"]),
        Compressor(threshold_db=s["compressor_threshold"],
                   ratio=s["compressor_ratio"]),
        Limiter(threshold_db=s["limiter_threshold"]),
        HighShelfFilter(cutoff_frequency_hz=s["high_shelf_hz"],
                        gain_db=s["high_shelf_db"]),
        LowShelfFilter(cutoff_frequency_hz=s["low_shelf_hz"],
                       gain_db=s["low_shelf_db"]),
        Reverb(room_size=s["reverb_room_size"],
               wet_level=s["reverb_wet_level"]),
        Delay(delay_seconds=s["delay_seconds"],
              feedback=s["delay_feedback"],
              mix=s["delay_mix"]),
    ])


def apply_audio_chain(input_file: str, output_file: str,
                      settings: dict = None) -> None:
    """Apply a professional broadcast audio chain to a WAV or MP3 file.

    Args:
        input_file:  Path to input audio (WAV or MP3).
        output_file: Path to write processed WAV output.
        settings:    Optional dict to override default chain settings.
                     Keys match CHAIN_SETTINGS above.

    Raises:
        FileNotFoundError: If input_file does not exist.
        RuntimeError:      If audio processing fails.
    """
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")

    s   = {**CHAIN_SETTINGS, **(settings or {})}
    tmp = None

    try:
        audio, samplerate, tmp = _load_audio(input_file)
        logger.debug("Loaded audio: %s  rate=%d  shape=%s",
                     input_file, samplerate, audio.shape)

        board          = _build_chain(s)
        processed      = board(audio, samplerate)

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

        # Write atomically via temp file
        tmp_out = output_file + ".tmp.wav"
        try:
            sf.write(tmp_out, processed, samplerate)
            os.replace(tmp_out, output_file)
        except Exception:
            if os.path.exists(tmp_out):
                os.unlink(tmp_out)
            raise

        logger.info("Audio chain applied: %s → %s", input_file, output_file)

    except Exception as e:
        logger.exception("Audio chain failed for %s: %s", input_file, e)
        raise RuntimeError(f"Audio chain failed for {input_file}: {e}") from e

    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError as e:
                logger.warning("Could not delete temp file %s: %s", tmp, e)
