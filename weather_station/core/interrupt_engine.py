import os
from audio_engine import audio_engine

PRIORITY_FOLDER = "audio/alerts/tornado"
ALERT_TONE_FOLDER = "audio/alert_tones"

ALERT_TONE_MAP = {
    "fire": "fire.wav",
    "flooding": "flood.wav",
    "freeze": "freeze.wav",
    "tornado": "tornado.wav",
    "thunderstorm": "thunderstorm.wav"
}

def play_alert_with_tone(alert_path, alert_type):
    tone_file = ALERT_TONE_MAP.get(alert_type.lower())

    tone_path = None
    if tone_file:
        tone_path = f"audio/alert_tones/{tone_file}"

    audio_engine.duck_and_play(alert_path, tone_path)


def check_priority_alert():
    files = [f for f in os.listdir(PRIORITY_FOLDER) if f.endswith(".wav")]
    return files

def interrupt_if_needed():
    alerts = check_priority_alert()
    if alerts:
        alert_file = os.path.join(PRIORITY_FOLDER, alerts[0])

        tone_files = os.listdir(ALERT_TONE_FOLDER)
        tone_path = None
        if tone_files:
            tone_path = os.path.join(ALERT_TONE_FOLDER, tone_files[0])

        audio_engine.duck_and_play(alert_file, tone_path)
