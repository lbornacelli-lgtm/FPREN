import os
import logging
import random

# Maps NWS event type strings (lowercase) to audio_playlist/alerts/ subfolder names.
# Priority-1 events get their own folder for immediate interrupt handling.
ALERT_FOLDER_MAP = {
    # Tornado
    "tornado emergency":            "priority_1",
    "tornado warning":              "tornado",
    "tornado watch":                "tornado",
    # Thunderstorm
    "severe thunderstorm warning":  "thunderstorm",
    "severe thunderstorm watch":    "thunderstorm",
    # Flooding
    "flash flood emergency":        "priority_1",
    "flash flood warning":          "flooding",
    "flash flood watch":            "flooding",
    "flood warning":                "flooding",
    "flood watch":                  "flooding",
    "coastal flood warning":        "flooding",
    "coastal flood watch":          "flooding",
    # Hurricane / Tropical
    "hurricane warning":            "hurricane",
    "hurricane watch":              "hurricane",
    "tropical storm warning":       "hurricane",
    "tropical storm watch":         "hurricane",
    "storm surge warning":          "hurricane",
    "storm surge watch":            "hurricane",
    "hurricane local statement":    "hurricane",
    "extreme wind warning":         "hurricane",
    "hurricane force wind warning": "hurricane",
    "hurricane force wind watch":   "hurricane",
    # Fog
    "dense fog advisory":           "fog",
    "freezing fog advisory":        "fog",
    "dense smoke advisory":         "fog",
    # Fire
    "red flag warning":             "fire",
    "fire weather watch":           "fire",
    "extreme fire danger":          "fire",
    # Freeze / Winter
    "freeze warning":               "freeze",
    "freeze watch":                 "freeze",
    "frost advisory":               "freeze",
    "hard freeze warning":          "freeze",
    "winter storm warning":         "freeze",
    "winter storm watch":           "freeze",
    "winter weather advisory":      "freeze",
    "ice storm warning":            "freeze",
    "blizzard warning":             "freeze",
    "cold weather advisory":        "freeze",
}

# Root of the broadcast-facing audio library
ALERT_AUDIO_ROOT = "/home/lh_admin/weather_station/audio/alerts"

# Shared audio playlist library (all non-alert content)
PLAYLIST_ROOT = "/home/lh_admin/audio_playlist"

# Subfolders checked in priority order for playback
ALERT_PRIORITY_ORDER = [
    "priority_1", "tornado", "thunderstorm", "hurricane",
    "flooding", "fire", "freeze", "fog", "other_alerts",
]


class FileRouter:
    def __init__(self, settings):
        self.logger = logging.getLogger("FileRouter")
        self.settings = settings

        audio = settings.AUDIO_PATH
        self.audio_dirs = {
            # Station audio library (files live here)
            "top_of_hour":         os.path.join(audio, "top_of_hour"),
            "educational":         os.path.join(audio, "educational"),
            "imaging":             os.path.join(audio, "imaging"),
            "alerts":              os.path.join(audio, "alerts"),
            "traffic":             os.path.join(audio, "traffic"),
            "weather":             os.path.join(audio, "weather"),
            "generated_wav_files": os.path.join(audio, "generated_wav_files"),
            # Shared broadcast library (populated separately)
            "pl_top_of_hour":         os.path.join(PLAYLIST_ROOT, "top_of_the_hour"),
            "pl_educational":         os.path.join(PLAYLIST_ROOT, "educational"),
            "pl_imaging":             os.path.join(PLAYLIST_ROOT, "imaging"),
            "pl_traffic":             os.path.join(PLAYLIST_ROOT, "traffic"),
            "pl_weather":             os.path.join(PLAYLIST_ROOT, "weather"),
            "pl_generated_wav_files": os.path.join(PLAYLIST_ROOT, "generated_wav_files"),
        }
        self.logger.info("FileRouter initialized")

    def route_alert_by_event(self, event_type: str) -> str:
        """
        Map a NWS event type string to the correct audio_playlist/alerts/ subfolder.
        Returns the full directory path, creating it if needed.
        """
        subfolder = ALERT_FOLDER_MAP.get(event_type.lower().strip(), "other_alerts")
        path = os.path.join(ALERT_AUDIO_ROOT, subfolder)
        os.makedirs(path, exist_ok=True)
        self.logger.debug(f"Routing '{event_type}' → {path}")
        return path

    def get_next_alert_file(self) -> str | None:
        """
        Return the oldest pending alert WAV across all subfolders, checking
        higher-priority subfolders first (priority_1 → tornado → … → other_alerts).
        Returns None when no alert WAVs are waiting.
        """
        for subfolder in ALERT_PRIORITY_ORDER:
            folder = os.path.join(ALERT_AUDIO_ROOT, subfolder)
            if not os.path.isdir(folder):
                continue
            files = sorted(
                f for f in os.listdir(folder)
                if f.lower().endswith(".wav") and "_processed" not in f
            )
            if files:
                path = os.path.join(folder, files[0])
                self.logger.info(f"Next alert: {path}")
                return path
        return None

    def get_next_file(self, category="educational"):
        folder = self.audio_dirs.get(category)
        if not folder or not os.path.isdir(folder):
            self.logger.warning(f"No folder for category '{category}'")
            return None

        files = [f for f in os.listdir(folder) if f.lower().endswith(".wav") and "_processed" not in f]
        if not files:
            self.logger.warning(f"No WAV files in '{folder}'")
            return None

        next_file = os.path.join(folder, random.choice(files))
        self.logger.info(f"Next file selected: {next_file}")
        return next_file
