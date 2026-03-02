import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template_string
from flask_socketio import SocketIO
from pymongo import MongoClient
from datetime import datetime
import threading
import time

# -----------------------
# Flask / Socket Setup
# -----------------------

app = Flask(__name__)
socketio = SocketIO(app, async_mode="eventlet")

# -----------------------
# MongoDB
# -----------------------

client = MongoClient("mongodb://localhost:27017/")
db = client["weather_rss"]

# -----------------------
# HTML Template
# -----------------------

HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
    <title>Weather RSS Dashboard</title>
    <script src="https://cdn.socket.io/4.0.1/socket.io.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: Arial; background: #0f172a; color: white; }
        .feed { padding: 10px; margin: 5px; border-radius: 6px; cursor:pointer; }
        .fresh { background: #065f46; }
        .stale { background: #7f1d1d; }
        .container { display:flex; }
        .left { width: 40%; }
        .right { width: 60%; }
    </style>
</head>
<body>
<h1>Weather RSS Dashboard</h1>

<div class="container">
    <div class="left">
        <h2>Feeds</h2>
        <div id="feeds"></div>
    </div>

    <div class="right">
        <h2>History</h2>
        <canvas id="historyChart"></canvas>
        <h2>Feed Distribution</h2>
        <canvas id="pieChart"></canvas>
    </div>
</div>

<script>
    var socket = io();

    socket.on("update", function(data) {
        let feedsDiv = document.getElementById("feeds");
        feedsDiv.innerHTML = "";

        data.feeds.forEach(function(feed) {
            let div = document.createElement("div");
            div.className = "feed " + (feed.stale ? "stale" : "fresh");
            div.innerHTML = "<b>" + feed.name + "</b><br>Last fetched: "
                            + feed.last_fetched + "<br>Items: "
                            + feed.count;
            feedsDiv.appendChild(div);
        });

        updateCharts(data.history, data.distribution);
    });

    let historyChart = new Chart(
        document.getElementById("historyChart"),
        {
            type: "line",
            data: {
                labels: [],
                datasets: [{
                    label: "Minutes Ago",
                    data: []
                }]
            }
        }
    );

    let pieChart = new Chart(
        document.getElementById("pieChart"),
        {
            type: "pie",
            data: {
                labels: [],
                datasets: [{
                    data: []
                }]
            }
        }
    );

    function updateCharts(history, distribution) {
        historyChart.data.labels = history.labels;
        historyChart.data.datasets[0].data = history.values;
        historyChart.update();

        pieChart.data.labels = distribution.labels;
        pieChart.data.datasets[0].data = distribution.values;
        pieChart.update();
    }
</script>

</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

# -----------------------
# Background Update Loop
# -----------------------

def update_loop():
    while True:
        try:
            now = datetime.utcnow()

            # Latest per feed
            pipeline = [
                {"$sort": {"fetched_at": -1}},
                {"$group": {
                    "_id": "$feed_name",
                    "last_fetched": {"$first": "$fetched_at"},
                    "count": {"$sum": 1}
                }}
            ]

            results = list(db.feed_history.aggregate(pipeline))

            feeds = []

            for r in results:
                last_fetched = r["last_fetched"]

                # Normalize datetime (remove timezone if present)
                if last_fetched.tzinfo is not None:
                    last_fetched = last_fetched.replace(tzinfo=None)

                delta = now - last_fetched

                feeds.append({
                    "name": r["_id"],
                    "last_fetched": last_fetched.strftime("%Y-%m-%d %H:%M:%S"),
                    "count": r["count"],
                    "stale": delta.total_seconds() > 3600
                })

            # History (last 20)
            history_docs = list(
                db.feed_history.find().sort("fetched_at", -1).limit(20)
            )

            history_labels = []
            history_values = []

            for h in history_docs:
                fetched = h["fetched_at"]

                if fetched.tzinfo is not None:
                    fetched = fetched.replace(tzinfo=None)

                history_labels.append(h["feed_name"])
                history_values.append(
                    (now - fetched).total_seconds() / 60
                )

            # Distribution
            distribution_pipeline = [
                {"$group": {
                    "_id": "$feed_name",
                    "count": {"$sum": 1}
                }}
            ]

            dist_results = list(db.feed_history.aggregate(distribution_pipeline))

            dist_labels = [d["_id"] for d in dist_results]
            dist_values = [d["count"] for d in dist_results]

            socketio.emit("update", {
                "feeds": feeds,
                "history": {
                    "labels": history_labels,
                    "values": history_values
                },
                "distribution": {
                    "labels": dist_labels,
                    "values": dist_values
                }
            })

        except Exception as e:
            print("Error in update loop:", e)

        time.sleep(10)

# -----------------------
# Start Background Thread
# -----------------------

threading.Thread(target=update_loop, daemon=True).start()

# -----------------------
# Run App
# -----------------------

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
