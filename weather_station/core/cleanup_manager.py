import os
import time
from pymongo import MongoClient
from config import MONGO_URI

def cleanup_expired_alerts():
    client = MongoClient(MONGO_URI)
    db = client.weather_db

    expired = db.alerts.find({"expires": {"$lt": time.time()}})

    for alert in expired:
        filename = alert.get("wav_file")
        if filename and os.path.exists(filename):
            os.remove(filename)

        db.alerts.update_one(
            {"_id": alert["_id"]},
            {"$set": {"active": False}}
        )
