"""
playlist_engine.py

Builds and runs the hourly broadcast playlist for FPREN Weather Station.

Playlist order per hour:
  1. Top of hour
  2. Imaging / sweepers
  3. Weather
  4. Traffic
  5. Alerts (priority order: priority_1, tornado, thunderstorm,
             hurricane, fire, flooding, freeze, fog, other_alerts)
  6. Airport weather
  7. Educational

Priority-1 alerts (tornado emergency, flash flood emergency, extreme/severe)
interrupt any running playlist immediately via interrupt_engine.
"""

import logging
import os
from datetime import datetime, timedelta

from core.audio_engine import AudioEngine
from services.playback_tracker import PlaybackTracker

logger = logging.getLogger(__name__)

# Alert subfolders in full priority order (used when P1 content is present)
ALERT_PRIORITY = [
    "priority_1",
    "tornado",
    "thunderstorm",
    "hurricane",
    "fire",
    "flooding",
    "freeze",
    "fog",
    "other_alerts",
]

# P1 alert types — handled by interrupt_engine as immediate preemptions.
# These are excluded from the normal-mode hourly playlist.
P1_ALERT_TYPES = {"priority_1", "tornado", "thunderstorm", "hurricane"}

# Normal-mode alert types — play in regular hourly rotation when no P1 is active.
# Configurable per zone via zone_definitions.normal_mode_types in MongoDB.
NORMAL_ALERT_TYPES = [t for t in ALERT_PRIORITY if t not in P1_ALERT_TYPES]

# Default folder structure — override via constructor
DEFAULT_FOLDERS = {
    "top_of_hour":    "audio/zones/all_florida/top_of_hour",
    "imaging":        "audio/zones/all_florida/imaging",
    "weather":        "audio/zones/all_florida/weather",
    "traffic":        "audio/zones/all_florida/traffic",
    "alerts":         "audio/zones/all_florida",
    "airport":        "audio/zones/all_florida/airport_weather",
    "educational":    "audio/zones/all_florida/educational",
}


class PlaylistEngine:
    """Builds and plays the hourly broadcast playlist.

    Args:
        folders:    Dict of folder paths. Missing keys fall back to DEFAULT_FOLDERS.
        fm_enabled: Whether to route audio through FM transmitter.
        zone:       Zone name for logging (default 'all_florida').
    """

    def __init__(self, folders: dict = None, fm_enabled: bool = False,
                 zone: str = "all_florida"):
        self.folders  = {**DEFAULT_FOLDERS, **(folders or {})}
        self.zone     = zone
        self.audio    = AudioEngine(fm_enabled=fm_enabled)
        self._tracker = PlaybackTracker()
        logger.info("PlaylistEngine initialized (zone=%s, fm=%s)", zone, fm_enabled)

    def _list(self, folder: str) -> list:
        """Safely list audio files in a folder — returns [] if folder missing."""
        if not folder or not os.path.isdir(folder):
            logger.debug("Folder not found, skipping: %s", folder)
            return []
        files = self.audio.list_wavs(folder)
        logger.debug("Found %d file(s) in %s", len(files), folder)
        return files

    def build_playlist(self, normal_mode: bool = True,
                       alert_types: list = None) -> list:
        """Build one-hour playlist in broadcast order.

        Args:
            normal_mode: If True (default), exclude P1 alert types
                         (priority_1, tornado, thunderstorm, hurricane) —
                         those are handled by interrupt_engine. Pass False
                         to include all alert types.
            alert_types: Override the alert type list. If None, uses
                         NORMAL_ALERT_TYPES (when normal_mode=True) or
                         ALERT_PRIORITY (when normal_mode=False).

        Returns:
            Ordered list of audio file paths.
        """
        if alert_types is None:
            alert_types = NORMAL_ALERT_TYPES if normal_mode else ALERT_PRIORITY

        playlist = []

        # 1. Top of hour
        playlist.extend(self._list(self.folders["top_of_hour"]))

        # 2. Imaging / sweepers
        playlist.extend(self._list(self.folders["imaging"]))

        # 3. Weather
        playlist.extend(self._list(self.folders["weather"]))

        # 4. Traffic
        playlist.extend(self._list(self.folders["traffic"]))

        # 5. Alerts in configured order
        alerts_root = self.folders["alerts"]
        for alert_type in alert_types:
            folder = os.path.join(alerts_root, alert_type)
            files  = self._list(folder)
            if files:
                logger.info("Adding %d %s alert(s) to playlist", len(files), alert_type)
            playlist.extend(files)

        # 6. Airport weather
        playlist.extend(self._list(self.folders.get("airport", "")))

        # 7. Educational
        playlist.extend(self._list(self.folders["educational"]))

        logger.info("Playlist built: %d file(s) for zone=%s", len(playlist), self.zone)
        return playlist

    def run_hour(self) -> float:
        """Play the hourly playlist and return seconds until next top-of-hour.

        Returns:
            Seconds remaining until the next top-of-hour mark.
        """
        playlist = self.build_playlist()

        if not playlist:
            logger.warning("Playlist is empty for zone=%s — nothing to play.", self.zone)
        else:
            logger.info("Starting playback: %d file(s)", len(playlist))

        for audio_file in playlist:
            try:
                self.audio.play(audio_file)
                self._tracker.record_play(audio_file)
            except Exception as e:
                logger.error("Playback failed for %s: %s — skipping.", audio_file, e)

        return self._seconds_until_next_hour()

    def _seconds_until_next_hour(self) -> float:
        """Return seconds from now until the next top-of-hour mark."""
        now       = datetime.now()
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        remaining = (next_hour - now).total_seconds()
        logger.debug("Next top-of-hour in %.1f seconds", remaining)
        return remaining

    def get_playlist_summary(self) -> dict:
        """Return a summary of what's in the current playlist by category.

        Useful for the dashboard and station monitoring.
        """
        summary = {}
        summary["top_of_hour"] = len(self._list(self.folders["top_of_hour"]))
        summary["imaging"]     = len(self._list(self.folders["imaging"]))
        summary["weather"]     = len(self._list(self.folders["weather"]))
        summary["traffic"]     = len(self._list(self.folders["traffic"]))
        summary["educational"] = len(self._list(self.folders["educational"]))
        summary["airport"]     = len(self._list(self.folders.get("airport", "")))

        alerts_root = self.folders["alerts"]
        summary["alerts"] = {}
        for alert_type in ALERT_PRIORITY:
            folder = os.path.join(alerts_root, alert_type)
            count  = len(self._list(folder))
            if count:
                summary["alerts"][alert_type] = count

        summary["total"] = sum([
            summary["top_of_hour"], summary["imaging"], summary["weather"],
            summary["traffic"],     summary["educational"], summary["airport"],
            sum(summary["alerts"].values()),
        ])
        return summary
