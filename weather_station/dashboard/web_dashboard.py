from flask import Flask, render_template_string, jsonify
import os

app = Flask(__name__)

HTML = """
<html>
<head>
<title>Weather Station Control</title>
<style>
body { background:#111; color:#0f0; font-family:Arial; }
button { padding:10px; margin:5px; font-size:16px; }
.card { border:1px solid #0f0; padding:15px; margin:10px; }
</style>
</head>
<body>

<h1>Weather Broadcast Control Panel</h1>

<div class="card">
<h2>System Status</h2>
<p id="status">Running</p>
<button onclick="location.reload()">Refresh</button>
</div>

<div class="card">
<h2>Controls</h2>
<button onclick="fetch('/restart')">Restart Station</button>
<button onclick="fetch('/cleanup')">Cleanup Alerts</button>
</div>

</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/restart")
def restart():
    os.system("systemctl restart weatherstation")
    return jsonify({"status":"restarting"})

@app.route("/cleanup")
def cleanup():
    os.system("python cleanup_manager.py")
    return jsonify({"status":"cleaned"})

def run_dashboard():
    app.run(host="0.0.0.0", port=5000)
