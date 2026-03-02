# services/mongo_service.py
from pymongo import MongoClient
import os
import logging

class MongoService:
    def __init__(self):
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.client = MongoClient(mongo_uri)
        self.db = self.client.get_database(os.getenv("MONGO_DB", "weather_station"))
        logging.info("MongoService connected to MongoDB")

    def fetch_alerts(self):
        """Fetch active alerts from MongoDB collection 'alerts'"""
        try:
            alerts_col = self.db.alerts
            alerts = list(alerts_col.find({"active": True}))
            result = []
            for a in alerts:
                result.append({
                    "id": str(a["_id"]),
                    "type": a.get("type", "other_alerts"),
                    "message": a.get("message", "No message")
                })
            return result
        except Exception as e:
            logging.error(f"MongoService fetch_alerts error: {e}")
            return []

    def fetch_weather_xml(self):
        """Fetch weather XMLs from MongoDB collection 'weather'"""
        try:
            weather_col = self.db.weather
            items = list(weather_col.find({}))
            result = []
            for w in items:
                result.append({
                    "id": str(w["_id"]),
                    "xml_content": w.get("xml_content", "")
                })
            return result
        except Exception as e:
            logging.error(f"MongoService fetch_weather_xml error: {e}")
            return []
