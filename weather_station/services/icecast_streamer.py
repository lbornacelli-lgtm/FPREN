"""IcecastStreamer — pipes WAV audio into Icecast via FFmpeg and a named FIFO.

Stream flow:
    WAV file → enqueue()
                └─ feeder thread
                      ├─ reads WAV with soundfile (int16)
                      ├─ resamples to TARGET_RATE / mono via scipy if needed
                      └─ writes raw PCM to FIFO in CHUNK_SECONDS-sized bursts
                              └─ FFmpeg reads FIFO → MP3 128 kbps → Icecast
"""

import errno
import logging
import os
import queue
import subprocess
import threading
import time

import numpy as np

logger = logging.getLogger("IcecastStreamer")

TARGET_RATE      = 22050   # Hz
CHANNELS         = 1       # mono
BYTES_PER_SAMPLE = 2       # int16 / s16le
CHUNK_SECONDS    = 0.1     # 100 ms per write burst

# Number of bytes in one chunk: 22050 * 0.1 * 1 * 2 = 4410
CHUNK_BYTES   = int(TARGET_RATE * CHUNK_SECONDS * CHANNELS * BYTES_PER_SAMPLE)
SILENCE_CHUNK = bytes(CHUNK_BYTES)

FIFO_PATH    = "/tmp/beacon_stream.fifo"
FIFO_TIMEOUT = 15  # seconds to wait for FFmpeg to open the FIFO


class IcecastStreamer:
    """Enqueue WAV paths; background thread streams them to Icecast."""

    def __init__(self, settings):
        self.settings    = settings
        self._queue      = queue.Queue()
        self._thread     = None
        self._ffmpeg     = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start the feeder thread (also starts FFmpeg)."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._feeder_loop, daemon=True, name="IcecastFeeder"
        )
        self._thread.start()
        logger.info(
            "IcecastStreamer started → http://%s:%d%s",
            self.settings.ICECAST_HOST,
            self.settings.ICECAST_PORT,
            self.settings.ICECAST_MOUNT,
        )

    def enqueue(self, wav_path):
        """Add a WAV file path to the playback queue."""
        if os.path.isfile(wav_path):
            self._queue.put(wav_path)
        else:
            logger.warning("enqueue: file not found: %s", wav_path)

    def stop(self):
        """Stop streaming and terminate FFmpeg."""
        self._stop_event.set()
        if self._ffmpeg and self._ffmpeg.poll() is None:
            self._ffmpeg.terminate()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("IcecastStreamer stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _icecast_url(self):
        s = self.settings
        return (
            f"icecast://source:{s.ICECAST_SOURCE_PASSWORD}@"
            f"{s.ICECAST_HOST}:{s.ICECAST_PORT}{s.ICECAST_MOUNT}"
        )

    def _ensure_fifo(self):
        if os.path.exists(FIFO_PATH):
            os.remove(FIFO_PATH)
        os.mkfifo(FIFO_PATH)

    def _start_ffmpeg(self):
        cmd = [
            "ffmpeg", "-loglevel", "warning",
            "-f", "s16le",
            "-ar", str(TARGET_RATE),
            "-ac", str(CHANNELS),
            "-i", FIFO_PATH,
            "-c:a", "libmp3lame", "-b:a", "128k",
            "-ac", "2",          # upmix mono → stereo (Icecast expects stereo)
            "-f", "mp3",
            "-content_type", "audio/mpeg",
            "-ice_name", "Beacon - All Florida",
            "-ice_description", "This is a stream of the Beacon Emergency Alerting System.",
            self._icecast_url(),
        ]
        self._ffmpeg = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        logger.info("FFmpeg started (PID %d)", self._ffmpeg.pid)

    def _open_fifo(self):
        """Open the FIFO for writing without blocking forever.

        Uses O_NONBLOCK so we can poll while the stop event isn't set and
        FFmpeg hasn't given up on the read side.
        """
        deadline = time.monotonic() + FIFO_TIMEOUT
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            try:
                fd = os.open(FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
                return os.fdopen(fd, "wb", buffering=0)
            except OSError as exc:
                if exc.errno == errno.ENXIO:  # no reader on the other end yet
                    time.sleep(0.2)
                else:
                    raise
        logger.error("Timed out waiting for FFmpeg to open FIFO")
        return None

    def _resample(self, data, src_rate):
        """Resample int16 mono array from src_rate to TARGET_RATE."""
        from scipy.signal import resample as scipy_resample
        n_out = int(len(data) * TARGET_RATE / src_rate)
        return scipy_resample(data, n_out).astype(np.int16)

    # ------------------------------------------------------------------
    # Feeder loop
    # ------------------------------------------------------------------

    def _feeder_loop(self):
        import soundfile as sf

        while not self._stop_event.is_set():
            self._ensure_fifo()
            self._start_ffmpeg()

            fifo = self._open_fifo()
            if fifo is None:
                if self._ffmpeg and self._ffmpeg.poll() is None:
                    self._ffmpeg.terminate()
                time.sleep(10)  # give Icecast time to release the mount
                continue

            try:
                with fifo:
                    logger.info("FIFO open — streaming to Icecast")
                    while not self._stop_event.is_set():
                        # Detect dead FFmpeg before the next write
                        if self._ffmpeg and self._ffmpeg.poll() is not None:
                            logger.warning(
                                "FFmpeg exited (code %d) — restarting",
                                self._ffmpeg.returncode,
                            )
                            break

                        try:
                            wav_path = self._queue.get(timeout=CHUNK_SECONDS)
                        except queue.Empty:
                            fifo.write(SILENCE_CHUNK)
                            continue

                        # Read + normalise audio
                        try:
                            data, sr = sf.read(wav_path, dtype="int16", always_2d=False)
                        except Exception as exc:
                            logger.error("Cannot read WAV %s: %s", wav_path, exc)
                            continue

                        if data.ndim > 1:
                            data = data.mean(axis=1).astype(np.int16)
                        if sr != TARGET_RATE:
                            data = self._resample(data, sr)

                        raw = data.tobytes()
                        for offset in range(0, len(raw), CHUNK_BYTES):
                            if self._stop_event.is_set():
                                break
                            chunk = raw[offset : offset + CHUNK_BYTES]
                            if len(chunk) < CHUNK_BYTES:
                                chunk = chunk + bytes(CHUNK_BYTES - len(chunk))
                            fifo.write(chunk)
                            time.sleep(CHUNK_SECONDS)

            except BrokenPipeError:
                logger.warning("FIFO broken pipe — restarting FFmpeg")
            except Exception as exc:
                logger.error("Streamer error: %s", exc)
                time.sleep(1)

            # Ensure FFmpeg is gone before restarting the loop
            if self._ffmpeg and self._ffmpeg.poll() is None:
                self._ffmpeg.terminate()
                self._ffmpeg.wait(timeout=3)
            # Give Icecast time to release the mount before reconnecting
            if not self._stop_event.is_set():
                logger.info("Waiting 10s for Icecast to release mount before reconnecting...")
                time.sleep(10)
