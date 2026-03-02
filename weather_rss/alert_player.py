# alert_player.py
import os
from queue import Queue
from threading import Thread
from pydub import AudioSegment
import simpleaudio as sa
from pymongo import MongoClient
from datetime import datetime, timezone

ALERT_DIR = "/home/lh_admin/weather_rss/audio/alerts"
NORMAL_PLAYLIST_DIR = "/home/lh_admin/weather_rss/audio/playlist"

client = MongoClient("mongodb://localhost:27017/")
db = client["weather"]
feeds = db["feeds"]

# Queue for alerts
alert_queue = Queue()

# Track currently playing alert
current_alert = None

# Map alert title keywords to WAV filenames
ALERT_WAV_MAP = {
    "Tornado": "Tornado.wav",
    "Severe": "SevereThunderstorm.wav",
    "Flood": "Flood.wav",
    "Other": "Other.wav"
}

def fetch_new_alerts():
    # Fetch unplayed alerts from Mongo
    alerts = feeds.find({"played": {"$ne": True}}).sort([("priority",-1),("fetched_at",1)])
    for a in alerts:
        alert_queue.put(a)
        feeds.update_one({"_id": a["_id"]}, {"$set": {"queued_at": datetime.now(timezone.utc)}})

def play_wav(file_path):
    song = AudioSegment.from_wav(file_path)
    play_obj = sa.play_buffer(song.raw_data, num_channels=song.channels,
                              bytes_per_sample=song.sample_width,
                              sample_rate=song.frame_rate)
    play_obj.wait_done()

def alert_worker():
    global current_alert
    while True:
        if not alert_queue.empty():
            current_alert = alert_queue.get()
            print(f"[ALERT] Playing: {current_alert['title']}")
            wav_file = ALERT_WAV_MAP.get(current_alert['title'].split()[0], "Other.wav")
            wav_path = os.path.join(ALERT_DIR, wav_file)
            if os.path.exists(wav_path):
                play_wav(wav_path)
            feeds.update_one({"_id": current_alert["_id"]}, {"$set": {"played": True}})
            current_alert = None
        else:
            # Play normal playlist
            for f in os.listdir(NORMAL_PLAYLIST_DIR):
                if f.endswith(".wav"):
                    play_wav(os.path.join(NORMAL_PLAYLIST_DIR, f))

# Start alert worker thread
t = Thread(target=alert_worker, daemon=True)
t.start()
