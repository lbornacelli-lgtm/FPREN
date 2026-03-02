BASE_AUDIO_PATH = "audio"

MONGO_URI = "mongodb://localhost:27017"
DATABASE_NAME = "weather_db"

ALERT_COLLECTION = "alerts"
WEATHER_COLLECTION = "weather"

TTS_VOICE = "en-us"
SAMPLE_RATE = 22050

STATIONS = [
    {
        "name": "Station1",
        "audio_path": "stations/station1/audio",
        "mongo_filter": {"county": "Alachua"}
    },
    {
        "name": "Station2",
        "audio_path": "stations/station2/audio",
        "mongo_filter": {"county": "Marion"}
    }
]
