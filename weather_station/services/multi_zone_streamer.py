"""
multi_zone_streamer.py
──────────────────────
Manages one IcecastStreamer per zone. Each zone gets:
  - Its own FIFO at /tmp/fpren_{zone_id}_stream.fifo
  - Its own FFmpeg process → Icecast mount on port 8000
  - Continuous audio from weather_station/audio/zones/{zone_id}/ in playlist priority order
  - Automatic silence when no audio files are present for that zone

Zone definitions are loaded from MongoDB (zone_definitions collection), cross-referenced
with settings.ZONE_STREAMS for mount point assignments. Falls back to settings.ZONE_STREAMS
alone if MongoDB is unavailable.

Playlist priority order (mirrors PlaylistEngine):
  priority_1 → tornado → thunderstorm → hurricane → fire → flooding → freeze →
  fog → other_alerts → weather_report → traffic → airport_weather → educational →
  imaging → top_of_hour

Usage (standalone process):
    python -m weather_station.services.multi_zone_streamer

Usage (programmatic):
    mzs = MultiZoneStreamer()
    mzs.start()
    mzs.status()   # → {zone_id: {...}, ...}
    mzs.stop()
"""

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from weather_station.config.settings import Settings
from weather_station.services.icecast_streamer import (
    IcecastStreamer,
    TARGET_RATE,
    CHANNELS,
    CHUNK_BYTES,
    CHUNK_SECONDS,
    SILENCE_CHUNK,
)

logger = logging.getLogger("MultiZoneStreamer")

# Audio file extensions the feeder will enqueue
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac"}

# Subdirectory names in playlist priority order (highest priority first).
# Mirrors the ALERT_PRIORITY list in PlaylistEngine plus content categories.
PLAYLIST_ORDER = [
    "priority_1",
    "tornado",
    "thunderstorm",
    "hurricane",
    "fire",
    "flooding",
    "freeze",
    "fog",
    "other_alerts",
    "weather_report",
    "traffic",
    "airport_weather",
    "educational",
    "imaging",
    "top_of_hour",
]

# Zone audio root — relative to this file: services/ → .. → audio/zones/
_ZONES_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "audio", "zones")
)


# ─────────────────────────────────────────────────────────────────────────────
# Zone settings proxy
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ZoneSettings:
    """Minimal settings object accepted by IcecastStreamer, scoped to one zone."""

    ICECAST_HOST:            str
    ICECAST_PORT:            int
    ICECAST_SOURCE_PASSWORD: str
    ICECAST_MOUNT:           str
    zone_id:                 str
    name:                    str

    @classmethod
    def from_zone(cls, zone: dict, base: Settings) -> "ZoneSettings":
        return cls(
            ICECAST_HOST            = base.ICECAST_HOST,
            ICECAST_PORT            = base.ICECAST_PORT,
            ICECAST_SOURCE_PASSWORD = base.ICECAST_SOURCE_PASSWORD,
            ICECAST_MOUNT           = zone["mount"],
            zone_id                 = zone["zone_id"],
            name                    = zone["name"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# Zone-aware streamer (extends IcecastStreamer)
# ─────────────────────────────────────────────────────────────────────────────

class ZoneIcecastStreamer(IcecastStreamer):
    """IcecastStreamer extended for multi-zone use.

    Changes from the base class:
    - Per-zone FIFO path: /tmp/fpren_{zone_id}_stream.fifo
    - FFmpeg ice_name metadata set to zone name
    - Audio decode via FFmpeg subprocess (handles MP3 + WAV + any FFmpeg format)
      instead of soundfile (which cannot decode MP3 without extra plugins)
    """

    def __init__(self, zone_settings: ZoneSettings):
        super().__init__(zone_settings)
        self._fifo_path = f"/tmp/fpren_{zone_settings.zone_id}_stream.fifo"

    # ── FIFO helpers (override hardcoded path in base class) ─────────────────

    def _ensure_fifo(self):
        if os.path.exists(self._fifo_path):
            try:
                os.remove(self._fifo_path)
            except OSError:
                pass
        os.mkfifo(self._fifo_path)

    def _open_fifo(self):
        """Open the per-zone FIFO for writing, polling until FFmpeg is ready."""
        import errno
        deadline = time.monotonic() + 15  # FIFO_TIMEOUT equivalent
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            try:
                fd = os.open(self._fifo_path, os.O_WRONLY | os.O_NONBLOCK)
                return os.fdopen(fd, "wb", buffering=0)
            except OSError as exc:
                if exc.errno == errno.ENXIO:   # no reader yet
                    time.sleep(0.2)
                else:
                    raise
        logger.error(
            "Timed out waiting for FFmpeg to open FIFO for zone=%s",
            self.settings.zone_id,
        )
        return None

    # ── FFmpeg source process ────────────────────────────────────────────────

    def _start_ffmpeg(self):
        cmd = [
            "ffmpeg", "-loglevel", "warning",
            "-f", "s16le", "-ar", str(TARGET_RATE), "-ac", str(CHANNELS),
            "-i", self._fifo_path,
            "-c:a", "libmp3lame", "-b:a", "128k",
            "-ac", "2",                          # upmix mono → stereo
            "-f", "mp3",
            "-content_type", "audio/mpeg",
            "-ice_name",        self.settings.name,
            "-ice_description", "FPREN Florida Public Radio Emergency Network",
            "-ice_genre",       "FPREN",
            self._icecast_url(),
        ]
        self._ffmpeg = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        logger.info(
            "FFmpeg started for zone=%s mount=%s (PID %d)",
            self.settings.zone_id,
            self.settings.ICECAST_MOUNT,
            self._ffmpeg.pid,
        )

    # ── Audio decode ─────────────────────────────────────────────────────────

    def _decode_to_pcm(self, path: str) -> Optional[bytes]:
        """Decode any audio file (MP3, WAV, …) to raw s16le PCM via FFmpeg.

        Returns raw bytes at TARGET_RATE/mono, or None on error.
        Loads the full file into memory before streaming — acceptable for the
        short alert/weather clips used here (typically a few hundred KB).
        """
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-i", path,
            "-f", "s16le", "-ar", str(TARGET_RATE), "-ac", str(CHANNELS),
            "pipe:1",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                logger.error(
                    "FFmpeg decode failed for %s: %s",
                    path,
                    result.stderr.decode(errors="replace")[:300],
                )
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg decode timed out for %s", path)
            return None
        except Exception as exc:
            logger.error("Decode error for %s: %s", path, exc)
            return None

    # ── Feeder loop (replaces soundfile-based base implementation) ────────────

    def _feeder_loop(self):
        """Stream audio files from the queue to Icecast via FIFO.

        Uses FFmpeg for decoding so MP3 files are handled natively.
        Emits silence when the queue is empty (zone has no current audio).
        Restarts the FFmpeg → Icecast connection on pipe breaks or process death.
        """
        import queue as _queue

        while not self._stop_event.is_set():
            self._ensure_fifo()
            self._start_ffmpeg()

            fifo = self._open_fifo()
            if fifo is None:
                if self._ffmpeg and self._ffmpeg.poll() is None:
                    self._ffmpeg.terminate()
                time.sleep(10)
                continue

            try:
                with fifo:
                    logger.info(
                        "Streaming zone=%s → %s",
                        self.settings.zone_id,
                        self.settings.ICECAST_MOUNT,
                    )
                    while not self._stop_event.is_set():
                        # Detect dead FFmpeg before next write
                        if self._ffmpeg and self._ffmpeg.poll() is not None:
                            logger.warning(
                                "FFmpeg exited for zone=%s (code %d) — restarting",
                                self.settings.zone_id,
                                self._ffmpeg.returncode,
                            )
                            break

                        try:
                            audio_path = self._queue.get(timeout=CHUNK_SECONDS)
                        except _queue.Empty:
                            fifo.write(SILENCE_CHUNK)
                            continue

                        raw = self._decode_to_pcm(audio_path)
                        if raw is None:
                            continue  # skip bad file, stream continues

                        for offset in range(0, len(raw), CHUNK_BYTES):
                            if self._stop_event.is_set():
                                break
                            chunk = raw[offset : offset + CHUNK_BYTES]
                            if len(chunk) < CHUNK_BYTES:
                                chunk = chunk + bytes(CHUNK_BYTES - len(chunk))
                            fifo.write(chunk)
                            time.sleep(CHUNK_SECONDS)

            except BrokenPipeError:
                logger.warning(
                    "FIFO broken pipe for zone=%s — restarting FFmpeg",
                    self.settings.zone_id,
                )
            except Exception as exc:
                logger.error(
                    "Streamer error for zone=%s: %s", self.settings.zone_id, exc
                )
                time.sleep(1)

            if self._ffmpeg and self._ffmpeg.poll() is None:
                self._ffmpeg.terminate()
                self._ffmpeg.wait(timeout=3)

            if not self._stop_event.is_set():
                logger.info(
                    "Waiting 10s for Icecast to release mount=%s before reconnecting...",
                    self.settings.ICECAST_MOUNT,
                )
                time.sleep(10)


# ─────────────────────────────────────────────────────────────────────────────
# Zone playlist feeder
# ─────────────────────────────────────────────────────────────────────────────

class ZoneFeeder:
    """Thread that builds the audio playlist for one zone and feeds it into the streamer.

    Playlist priority order follows PLAYLIST_ORDER (alert subdirs first, then content).
    After each full cycle, the playlist is rebuilt so newly generated alert audio
    is picked up immediately. When the zone directory has no audio files, the feeder
    sleeps and the streamer emits silence automatically.

    The feeder keeps at most _QUEUE_AHEAD files pre-loaded in the streamer queue so
    that a freshly generated alert can be enqueued within a few seconds of the
    current file finishing, rather than waiting for the full playlist to drain.
    """

    _QUEUE_AHEAD = 2   # max files queued ahead in the streamer at any time

    def __init__(self, zone_id: str, streamer: ZoneIcecastStreamer,
                 zones_root: str = _ZONES_ROOT):
        self.zone_id  = zone_id
        self.zone_dir = os.path.normpath(os.path.join(zones_root, zone_id))
        self.streamer = streamer
        self._stop    = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._feed_loop,
            daemon=True,
            name=f"ZoneFeeder-{self.zone_id}",
        )
        self._thread.start()
        logger.info(
            "ZoneFeeder started for zone=%s (dir=%s)", self.zone_id, self.zone_dir
        )

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def build_playlist(self) -> list:
        """Return an ordered list of audio file paths for this zone.

        Walks each subdir in PLAYLIST_ORDER, collecting audio files sorted by name
        (so timestamp-named alert files play in generation order). Files directly
        in the zone root (not in a subdir) are appended last.
        """
        playlist: list = []
        known_subdirs = set(PLAYLIST_ORDER)

        if not os.path.isdir(self.zone_dir):
            logger.debug("Zone dir does not exist: %s", self.zone_dir)
            return playlist

        for subdir in PLAYLIST_ORDER:
            folder = os.path.join(self.zone_dir, subdir)
            if not os.path.isdir(folder):
                continue
            files = sorted(
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
            )
            playlist.extend(files)

        # Root-level audio files not in a known subdir
        for name in sorted(os.listdir(self.zone_dir)):
            if name in known_subdirs:
                continue
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                playlist.append(os.path.join(self.zone_dir, name))

        return playlist

    def _feed_loop(self):
        while not self._stop.is_set():
            playlist = self.build_playlist()

            if not playlist:
                logger.debug(
                    "No audio files for zone=%s — silence active; retrying in 30s",
                    self.zone_id,
                )
                self._stop.wait(30)
                continue

            logger.info(
                "Zone=%s playlist built: %d file(s)", self.zone_id, len(playlist)
            )

            for path in playlist:
                if self._stop.is_set():
                    break

                # Throttle: only push _QUEUE_AHEAD files at a time so the
                # playlist rebuild after each cycle picks up new alert audio quickly
                while (
                    not self._stop.is_set()
                    and self.streamer._queue.qsize() >= self._QUEUE_AHEAD
                ):
                    time.sleep(0.5)

                self.streamer.enqueue(path)

            # Brief gap between cycles (not self._stop.wait so we don't delay shutdown)
            if not self._stop.is_set():
                time.sleep(1)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-zone manager
# ─────────────────────────────────────────────────────────────────────────────

def _load_zone_list(settings: Settings) -> list:
    """Return the list of zone dicts to stream.

    Tries to load zone_ids from MongoDB zone_definitions, then filters
    settings.ZONE_STREAMS to those confirmed in the database.  Falls back to
    settings.ZONE_STREAMS without filtering if MongoDB is unavailable.
    """
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    try:
        from pymongo import MongoClient
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        db_zone_ids = {
            doc["zone_id"]
            for doc in client["weather_rss"]["zone_definitions"].find(
                {}, {"zone_id": 1, "_id": 0}
            )
        }
        client.close()
        if db_zone_ids:
            filtered = [z for z in settings.ZONE_STREAMS if z["zone_id"] in db_zone_ids]
            if filtered:
                logger.info(
                    "Loaded %d zone(s) from MongoDB zone_definitions", len(filtered)
                )
                return filtered
            logger.warning(
                "MongoDB zone_definitions returned no matching zones — "
                "falling back to settings.ZONE_STREAMS"
            )
    except Exception as exc:
        logger.warning(
            "MongoDB unavailable (%s) — using settings.ZONE_STREAMS", exc
        )
    return list(settings.ZONE_STREAMS)


class MultiZoneStreamer:
    """Manages one ZoneIcecastStreamer + ZoneFeeder per configured zone.

    Lifecycle:
        mzs = MultiZoneStreamer()
        mzs.start()          # launches all streamers and feeders
        mzs.status()         # returns per-zone status dict
        mzs.restart_zone(z)  # hot-restart one zone
        mzs.stop()           # graceful shutdown

    run() is a blocking convenience wrapper that handles SIGTERM/SIGINT:
        mzs.run()
    """

    def __init__(
        self,
        settings: Settings = None,
        zones_override: list = None,
        zones_root: str = _ZONES_ROOT,
    ):
        self._settings   = settings or Settings()
        self._zones_root = zones_root
        self._zones      = zones_override or _load_zone_list(self._settings)
        self._streamers: dict[str, ZoneIcecastStreamer] = {}
        self._feeders:   dict[str, ZoneFeeder]         = {}
        logger.info(
            "MultiZoneStreamer initialized with %d zone(s): %s",
            len(self._zones),
            [z["zone_id"] for z in self._zones],
        )

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        """Start all zone streamers and feeders."""
        for zone in self._zones:
            self._start_zone(zone)

    def stop(self):
        """Stop all feeders and streamers gracefully."""
        for feeder in self._feeders.values():
            feeder.stop()
        for streamer in self._streamers.values():
            streamer.stop()
        self._feeders.clear()
        self._streamers.clear()
        logger.info("MultiZoneStreamer stopped")

    def stop_zone(self, zone_id: str):
        """Stop the streamer and feeder for a single zone."""
        if zone_id in self._feeders:
            self._feeders.pop(zone_id).stop()
        if zone_id in self._streamers:
            self._streamers.pop(zone_id).stop()
        logger.info("Stopped zone=%s", zone_id)

    def restart_zone(self, zone_id: str):
        """Hot-restart a single zone without affecting the others."""
        self.stop_zone(zone_id)
        zone = next((z for z in self._zones if z["zone_id"] == zone_id), None)
        if zone is None:
            logger.error("restart_zone: unknown zone_id=%s", zone_id)
            return
        self._start_zone(zone)
        logger.info("Restarted zone=%s", zone_id)

    def _start_zone(self, zone: dict):
        zone_id  = zone["zone_id"]
        zs       = ZoneSettings.from_zone(zone, self._settings)
        streamer = ZoneIcecastStreamer(zs)
        feeder   = ZoneFeeder(zone_id, streamer, zones_root=self._zones_root)
        streamer.start()
        feeder.start()
        self._streamers[zone_id] = streamer
        self._feeders[zone_id]   = feeder
        logger.info(
            "Started zone=%s → %s:%d%s",
            zone_id, zs.ICECAST_HOST, zs.ICECAST_PORT, zs.ICECAST_MOUNT,
        )

    # ── Status ───────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Return a status snapshot for every managed zone.

        Keys per zone:
            mount        – Icecast mount point
            fifo_path    – path to the zone's FIFO file
            ffmpeg_pid   – PID of the FFmpeg process (or None)
            ffmpeg_live  – True if FFmpeg is running
            queue_depth  – number of files queued ahead in the streamer
        """
        out = {}
        for zone_id, streamer in self._streamers.items():
            ffmpeg_alive = (
                streamer._ffmpeg is not None and streamer._ffmpeg.poll() is None
            )
            out[zone_id] = {
                "mount":       streamer.settings.ICECAST_MOUNT,
                "fifo_path":   streamer._fifo_path,
                "ffmpeg_pid":  streamer._ffmpeg.pid if streamer._ffmpeg else None,
                "ffmpeg_live": ffmpeg_alive,
                "queue_depth": streamer._queue.qsize(),
            }
        return out

    # ── Blocking run (standalone process entry point) ─────────────────────────

    def run(self):
        """Start all zones and block until SIGTERM or KeyboardInterrupt."""
        self.start()
        logger.info(
            "MultiZoneStreamer running — %d zone(s) active", len(self._streamers)
        )

        stop_event = threading.Event()

        def _shutdown(signum, frame):
            logger.info("Shutdown signal %d received — stopping", signum)
            stop_event.set()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT,  _shutdown)

        try:
            stop_event.wait()
        finally:
            self.stop()
            logger.info("MultiZoneStreamer exited cleanly")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    MultiZoneStreamer().run()
