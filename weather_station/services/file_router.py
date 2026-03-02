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
ALERT_AUDIO_ROOT = "/home/lh_admin/audio_playlist/alerts"


class FileRouter:
    def __init__(self, settings):
        self.logger = logging.getLogger("FileRouter")
        self.settings = settings

        self.audio_dirs = {
            "top_of_hour": os.path.join(settings.AUDIO_PATH, "top_of_hour"),
            "educational": os.path.join(settings.AUDIO_PATH, "educational"),
            "imaging": os.path.join(settings.AUDIO_PATH, "imaging"),
            "alerts": os.path.join(settings.AUDIO_PATH, "alerts"),
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

    def get_next_file(self, category="educational"):
        folder = self.audio_dirs.get(category)
        if not folder or not os.path.isdir(folder):
            self.logger.warning(f"No folder for category '{category}'")
            return None

        files = [f for f in os.listdir(folder) if f.lower().endswith(".wav")]
        if not files:
            self.logger.warning(f"No WAV files in '{folder}'")
            return None

        next_file = os.path.join(folder, random.choice(files))
        self.logger.info(f"Next file selected: {next_file}")
        return next_file
