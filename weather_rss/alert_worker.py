import smtplib
from email.message import EmailMessage
from pymongo import MongoClient
from datetime import datetime

EMAIL_TO = "you@example.com"
EMAIL_FROM = "weather@server"
SMTP_SERVER = "localhost"

mongo = MongoClient()
db = mongo.weather_rss
col = db.feed_status

def send(subject, body):
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_SERVER) as s:
        s.send_message(msg)

for feed in col.find():
    if feed["status"] == "ERROR" and not feed.get("alerted"):
        send("Weather Feed FAILED", str(feed))
        col.update_one({"_id": feed["_id"]}, {"$set": {"alerted": True}})

    if feed["status"] == "OK" and feed.get("alerted"):
        send("Weather Feed RECOVERED", feed["filename"])
        col.update_one({"_id": feed["_id"]}, {"$unset": {"alerted": ""}})
