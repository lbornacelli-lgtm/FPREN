# weather_station/core/playlist_engine.py
import os
from datetime import datetime, timedelta
from core.audio_engine import AudioEngine

class PlaylistEngine:
    def __init__(self, folders, fm_enabled=False):
        """
        folders: dict with keys: top_of_hour, imaging, weather, traffic, alerts, educational
        """
        self.folders = folders
        self.audio = AudioEngine(fm_enabled=fm_enabled)

    def build_playlist(self):
        """Build one-hour playlist in required order"""
        playlist = []

        # Top of the Hour
        playlist.extend(self.audio.list_wavs(self.folders["top_of_hour"]))

        # Imaging rotation
        playlist.extend(self.audio.list_wavs(self.folders["imaging"]))

        # Weather
        playlist.extend(self.audio.list_wavs(self.folders["weather"]))

        # Traffic
        playlist.extend(self.audio.list_wavs(self.folders["traffic"]))

        # Alerts with priority
        alert_priority = ["fire", "flooding", "freeze", "tornado", "thunderstorm", "other_alerts"]
        for alert_type in alert_priority:
            folder = os.path.join(self.folders["alerts"], alert_type)
            playlist.extend(self.audio.list_wavs(folder))

        # Educational rotation
        playlist.extend(self.audio.list_wavs(self.folders["educational"]))

        return playlist

    def run_hour(self):
        """Run the playlist for one hour minus top-of-hour duration"""
        playlist = self.build_playlist()
        for wav_file in playlist:
            self.audio.play(wav_file)

        # Calculate sleep until next top-of-hour
        now = datetime.now()
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return (next_hour - now).total_seconds()
