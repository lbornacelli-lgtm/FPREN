from pymongo import MongoClient
from config import MONGO_URI
from interrupt_engine import interrupt_if_needed
import threading

def watch_alerts():
    client = MongoClient(MONGO_URI)
    db = client.weather_db
    collection = db.alerts

    with collection.watch() as stream:
        for change in stream:
            print("ALERT CHANGE DETECTED")
            interrupt_if_needed()

def start_watcher():
    t = threading.Thread(target=watch_alerts, daemon=True)
    t.start()
