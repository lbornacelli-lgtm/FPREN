# weather_station/core/playlist_engine.py
import os
import json
import logging
from datetime import datetime, timedelta
from core.audio_engine import AudioEngine
from services.playback_tracker import PlaybackTracker

PLAYLISTS_DIR        = "/home/ufuser/Fpren-main/weather_station/playlists"
STREAM_PLAYLISTS_FILE = "/home/ufuser/Fpren-main/weather_station/config/stream_playlists.json"

def _active_playlist_file():
    """Read stream_playlists.json and return the active playlist filename."""
    try:
        with open(STREAM_PLAYLISTS_FILE) as f:
            sp = json.load(f)
        return sp.get("stream_8000", "normal.json")
    except Exception:
        return "normal.json"

def _load_playlist_slots(filename):
    """Load slots from a playlist JSON file."""
    path = os.path.join(PLAYLISTS_DIR, filename)
    try:
        with open(path) as f:
            pl = json.load(f)
        return pl.get("slots", [])
    except Exception:
        return []

class PlaylistEngine:
    def __init__(self, folders=None, fm_enabled=False):
        """
        folders: dict with keys: top_of_hour, imaging, weather, traffic, alerts, educational
        If None, uses defaults from Settings.
        """
        self.folders = folders or {}
        self.audio = AudioEngine(fm_enabled=fm_enabled)
        self._tracker = PlaybackTracker()
        self.logger = logging.getLogger("PlaylistEngine")

    def build_playlist(self):
        """Build one-hour playlist from active playlist JSON slots."""
        active_file = _active_playlist_file()
        self.logger.info(f"Active playlist: {active_file}")

        # If mute.json or empty slots — return empty playlist (silence)
        slots = _load_playlist_slots(active_file)
        if not slots:
            self.logger.info("Playlist is empty or muted — no audio this hour.")
            return []

        playlist = []
        for slot in slots:
            category = slot.get("category", "")
            skip_if_empty = slot.get("skip_if_empty", False)
            top_of_hour = slot.get("top_of_hour", False)

            folder = self.folders.get(category)
            if not folder:
                self.logger.warning(f"No folder mapped for category: {category}")
                continue

            if category == "alerts":
                alert_priority = ["priority_1", "tornado", "thunderstorm", "hurricane",
                                  "flooding", "fire", "freeze", "fog", "other_alerts"]
                for alert_type in alert_priority:
                    subfolder = os.path.join(folder, alert_type)
                    wavs = self.audio.list_wavs(subfolder)
                    if wavs:
                        playlist.extend(wavs)
                        break
            else:
                wavs = self.audio.list_wavs(folder)
                if not wavs and skip_if_empty:
                    self.logger.info(f"Skipping empty slot: {slot.get('label','')}")
                    continue
                playlist.extend(wavs)

        return playlist

    def run_hour(self):
        """Run the playlist for one hour."""
        playlist = self.build_playlist()
        if not playlist:
            self.logger.info("Empty playlist — sleeping until next hour.")
        else:
            for wav_file in playlist:
                self.audio.play(wav_file)
                self._tracker.record_play(wav_file)

        # Sleep until next top-of-hour
        now = datetime.now()
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return (next_hour - now).total_seconds()
