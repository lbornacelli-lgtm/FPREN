# services/mongo_service.py
from pymongo import MongoClient
import os
import logging

class MongoService:
    def __init__(self):
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.client = MongoClient(mongo_uri)
        # weather_rss DB is where the RSS fetcher stores NWS alerts
        self.rss_db = self.client.get_database("weather_rss")
        self.db = self.client.get_database(os.getenv("MONGO_DB", "weather_station"))
        logging.info("MongoService connected to MongoDB")

    def fetch_unprocessed_alerts(self):
        """Fetch NWS alerts from weather_rss that have not yet had TTS generated."""
        try:
            col = self.rss_db.nws_alerts
            alerts = list(col.find({"tts_generated": {"$ne": True}}))
            result = []
            for a in alerts:
                result.append({
                    "alert_id": a.get("alert_id"),
                    "event":    a.get("event", "Special Statement"),
                    "headline": a.get("headline") or a.get("event", ""),
                    "description": a.get("description") or "",
                    "severity": a.get("severity", ""),
                    "area_desc": a.get("area_desc", ""),
                })
            return result
        except Exception as e:
            logging.error(f"MongoService fetch_unprocessed_alerts error: {e}")
            return []

    def mark_alert_tts_done(self, alert_id, wav_path):
        """Mark an alert as TTS-processed so it is not re-converted."""
        try:
            self.rss_db.nws_alerts.update_one(
                {"alert_id": alert_id},
                {"$set": {"tts_generated": True, "wav_path": wav_path}}
            )
        except Exception as e:
            logging.error(f"MongoService mark_alert_tts_done error: {e}")

    def fetch_alerts(self):
        """Fetch active alerts from local weather_station alerts collection."""
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
        """Fetch weather XMLs from MongoDB collection 'weather'."""
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
