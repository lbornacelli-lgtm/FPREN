#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from flask import Flask, render_template_string
from flask_socketio import SocketIO
from pymongo import MongoClient
import plotly
import plotly.graph_objects as go

# ---------------------------
# CONFIG
# ---------------------------
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "weather_rss"
STALE_THRESHOLD_MINUTES = 60  # Threshold for feed freshness
PORT = 5050
# ---------------------------

app = Flask(__name__)
socketio = SocketIO(app)

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# ---------------------------
# HTML template
# ---------------------------
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Weather RSS Dashboard</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
body { background-color: #1e1e1e; color: #eee; font-family: Arial, sans-serif; padding: 20px; }
h1 { color: #00ffff; margin-bottom: 20px; }
section { margin-bottom: 40px; }
.feed, .alert, .history-item { margin-bottom: 5px; padding: 5px; border-bottom: 1px solid #333; }
.ok { color: #0f0; }
.stale { color: #f00; }
</style>
</head>
<body>
<h1>Weather RSS Dashboard</h1>

<section>
<h2>Feed Freshness</h2>
<div id="feeds"></div>
</section>

<section>
<h2>Last 20 Fetch History</h2>
<div id="history"></div>
</section>

<section>
<h2>Active Alerts</h2>
<div id="alerts"></div>
</section>

<section>
<h2>Example Chart</h2>
<div id="chart"></div>
</section>

<script>
const feeds = {{ feeds_json | safe }};
const history = {{ history_json | safe }};
const alerts = {{ alerts_json | safe }};
const chartData = {{ chart_json | safe }};

// Feeds
document.getElementById("feeds").innerHTML = feeds.map(f => 
    `<div class="feed ${f.freshness.toLowerCase()}">
      ${f.feed_name} - Last fetched: ${f.last_fetched || 'NEVER'} [${f.freshness}]
    </div>`).join("");

// History
document.getElementById("history").innerHTML = history.map(h =>
    `<div class="history-item">
      ${h.feed_name} - ${h.title} (${h.fetched_at})
    </div>`).join("");

// Alerts
document.getElementById("alerts").innerHTML = alerts.map(a =>
    `<div class="alert">
      ${a.feed_name} - ${a.alert_type} (Start: ${a.start_time})
    </div>`).join("");

// Plotly Chart
Plotly.newPlot('chart', chartData.data, chartData.layout || {});
</script>
</body>
</html>
"""

# ---------------------------
# Helper functions
# ---------------------------
def get_feeds():
    feeds_list = []
    now = datetime.now(timezone.utc)
    for f in db.feed_status.find({}):
        feed_name = f.get("feed_name") or f.get("title") or "UNKNOWN_FEED"
        last_fetched = f.get("last_fetch")
        enabled = f.get("enabled", True)
        freshness = "STALE"
        if last_fetched:
            # Ensure last_fetched is timezone-aware
            if last_fetched.tzinfo is None:
                last_fetched = last_fetched.replace(tzinfo=timezone.utc)
            delta = now - last_fetched
            freshness = "OK" if delta.total_seconds() < STALE_THRESHOLD_MINUTES*60 else "STALE"
        feeds_list.append({
            "feed_name": feed_name,
            "last_fetched": str(last_fetched) if last_fetched else None,
            "status": "enabled" if enabled else "disabled",
            "freshness": freshness
        })
    return feeds_list

def get_history(limit=20):
    hist_list = []
    for h in db.feed_history.find().sort("fetched_at", -1).limit(limit):
        fetched_at = h.get("fetched_at")
        if fetched_at and fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        hist_list.append({
            "feed_name": h.get("feed_name") or "UNKNOWN_FEED",
            "title": h.get("title") or "No Title",
            "fetched_at": str(fetched_at) if fetched_at else "UNKNOWN"
        })
    return hist_list

def get_alerts():
    alert_list = []
    for a in db.feed_alerts.find({"active": True}):
        start_time = a.get("start_time")
        if start_time and start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        alert_list.append({
            "feed_name": a.get("feed_name") or "UNKNOWN_FEED",
            "alert_type": a.get("alert_type") or "UNKNOWN_ALERT",
            "start_time": str(start_time) if start_time else "UNKNOWN"
        })
    return alert_list

def get_chart(feeds):
    y_counts = [len(feeds), sum(1 for f in feeds if f['freshness']=='OK'), sum(1 for f in feeds if f['freshness']=='STALE')]
    fig = go.Figure(data=[go.Bar(y=y_counts, x=["Total Feeds","Fresh","Stale"])])
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)

# ---------------------------
# Routes
# ---------------------------
@app.route("/")
def dashboard():
    feeds = get_feeds()
    history = get_history()
    alerts = get_alerts()
    chart_json = get_chart(feeds)
    return render_template_string(
        DASHBOARD_HTML,
        feeds_json=json.dumps(feeds),
        history_json=json.dumps(history),
        alerts_json=json.dumps(alerts),
        chart_json=chart_json
    )

# ---------------------------
# Run app
# ---------------------------
if __name__ == "__main__":
    # Werkzeug allows unsafe in production with this flag
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)
