"""
web_dashboard.py

Lightweight Flask control panel for the FPREN Weather Station.
Provides system status, controls, and a live alert feed.

Runs on port 5000. Intended for local/LAN access only.
Do NOT expose this directly to the public internet without authentication.

Access: http://localhost:5000
"""

import logging
import os
import subprocess
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string

logger = logging.getLogger(__name__)
app    = Flask(__name__)

# ── Allowed commands (whitelist) ──────────────────────────────────────────────

ALLOWED_COMMANDS = {
    "restart": ["systemctl", "restart", "weatherstation"],
    "cleanup": ["python3",
                "/home/ufuser/Fpren-main/weather_station/core/cleanup_manager.py"],
}

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FPREN Weather Station Control</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body   { background: #111; color: #0f0; font-family: Arial, sans-serif; padding: 20px; }
    h1     { font-size: 1.6rem; margin-bottom: 16px;
             border-bottom: 1px solid #0f0; padding-bottom: 10px; }
    h2     { font-size: 1.1rem; margin-bottom: 10px; color: #0c0; }
    .grid  { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
             gap: 16px; margin-bottom: 20px; }
    .card  { border: 1px solid #0f0; border-radius: 6px; padding: 16px; }
    .badge { display: inline-block; padding: 3px 10px; border-radius: 4px;
             font-size: 0.85rem; font-weight: bold; }
    .ok    { background: #0a3; color: #fff; }
    .warn  { background: #a60; color: #fff; }
    .error { background: #a00; color: #fff; }
    button { padding: 9px 16px; margin: 4px 4px 4px 0; font-size: 0.95rem;
             border: 1px solid #0f0; background: #1a1a1a; color: #0f0;
             border-radius: 4px; cursor: pointer; transition: background 0.2s; }
    button:hover    { background: #0f0; color: #111; }
    button:disabled { opacity: 0.4; cursor: not-allowed; }
    #toast { position: fixed; bottom: 20px; right: 20px; padding: 10px 18px;
             background: #0a3; color: #fff; border-radius: 6px;
             display: none; font-size: 0.9rem; z-index: 999; }
    #toast.error { background: #a00; }
    table  { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #1e1e1e; }
    th     { color: #0c0; }
    .sev-extreme  { color: #f55; font-weight: bold; }
    .sev-severe   { color: #fa0; font-weight: bold; }
    .sev-moderate { color: #ff0; }
    #last-updated { font-size: 0.8rem; color: #070; margin-top: 8px; }

    /* ── Tab nav ── */
    .tab-nav {
      display: flex;
      align-items: center;
      border-bottom: 2px solid #0f0;
      margin-bottom: 20px;
      gap: 4px;
    }
    .tab-nav .spacer { flex: 1; }
    .tab-btn {
      padding: 8px 18px;
      background: #1a1a1a;
      border: 1px solid #0f0;
      border-bottom: none;
      color: #0f0;
      cursor: pointer;
      border-radius: 4px 4px 0 0;
      font-size: 0.95rem;
      transition: background 0.2s;
      margin-bottom: -2px;
    }
    .tab-btn:hover   { background: #0a3; }
    .tab-btn.active  { background: #0f0; color: #111; font-weight: bold; }
    .tab-panel       { display: none; }
    .tab-panel.active { display: block; }

    /* ── Config tab ── */
    .config-group { margin-bottom: 20px; }
    .config-group label { display: block; color: #0c0; margin-bottom: 4px; font-size: 0.9rem; }
    .config-group input, .config-group select {
      background: #1a1a1a; border: 1px solid #0f0; color: #0f0;
      padding: 6px 10px; border-radius: 4px; width: 100%; max-width: 400px;
      font-size: 0.9rem;
    }

    /* ── URL tab ── */
    .url-list { list-style: none; }
    .url-list li { padding: 8px 0; border-bottom: 1px solid #1e1e1e; font-size: 0.9rem; }
    .url-list a { color: #0f0; text-decoration: underline; }
    .url-list .url-label { color: #0c0; font-weight: bold; margin-right: 8px; }
  </style>
</head>
<body>
  <h1>&#127931; FPREN Weather Station Control Panel</h1>

  <!-- Tab navigation — Config and URL always last on the right -->
  <nav class="tab-nav">
    <button class="tab-btn active" onclick="switchTab('dashboard')">&#128202; Dashboard</button>
    <button class="tab-btn" onclick="switchTab('alerts')">&#128680; Alerts</button>
    <button class="tab-btn" onclick="switchTab('zones')">&#128205; Zones</button>
    <div class="spacer"></div>
    <button class="tab-btn" onclick="switchTab('config')">&#9881; Config</button>
    <button class="tab-btn" onclick="switchTab('urls')">&#128279; URLs</button>
  </nav>

  <!-- Dashboard tab -->
  <div id="tab-dashboard" class="tab-panel active">
    <div class="grid">
      <div class="card">
        <h2>System Status</h2>
        <p>Station: <span id="station-status" class="badge ok">Loading...</span></p>
        <p style="margin-top:8px">MongoDB:
          <span id="mongo-status" class="badge ok">Loading...</span></p>
        <p style="margin-top:8px">TTS Engine:
          <span class="badge ok">gTTS</span></p>
        <p id="last-updated"></p>
      </div>

      <div class="card">
        <h2>Active Alerts</h2>
        <p>Florida: <span id="fl-count" class="badge warn">--</span></p>
        <p style="margin-top:8px">Alachua County:
          <span id="alachua-count" class="badge warn">--</span></p>
        <p style="margin-top:8px">Airport Delays:
          <span id="airport-count" class="badge warn">--</span></p>
      </div>

      <div class="card">
        <h2>Controls</h2>
        <button id="btn-restart" onclick="runCommand('restart', this)">
          &#9654; Restart Station
        </button>
        <button id="btn-cleanup" onclick="runCommand('cleanup', this)">
          &#128465; Cleanup Alerts
        </button>
        <button onclick="loadStatus()">&#8635; Refresh Status</button>
      </div>
    </div>
  </div>

  <!-- Alerts tab -->
  <div id="tab-alerts" class="tab-panel">
    <div class="card">
      <h2>Recent Florida Alerts</h2>
      <table>
        <thead>
          <tr><th>Event</th><th>Severity</th><th>Area</th><th>Source</th></tr>
        </thead>
        <tbody id="alerts-table">
          <tr><td colspan="4">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Zones tab -->
  <div id="tab-zones" class="tab-panel">
    <div class="card">
      <h2>Zone Definitions</h2>
      <table>
        <thead>
          <tr><th>Zone</th><th>Type</th><th>Counties</th><th>Cleanup</th></tr>
        </thead>
        <tbody id="zones-table">
          <tr><td colspan="4">Loading...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Config tab (always second-to-last) -->
  <div id="tab-config" class="tab-panel">
    <div class="card">
      <h2>Station Configuration</h2>
      <div class="config-group">
        <label>MongoDB URI</label>
        <input type="text" value="mongodb://localhost:27017/" readonly>
      </div>
      <div class="config-group">
        <label>Zones Root</label>
        <input type="text" value="/home/ufuser/Fpren-main/weather_station/audio/zones" readonly>
      </div>
      <div class="config-group">
        <label>Zone Alert Interval (seconds)</label>
        <input type="text" value="60" readonly>
      </div>
      <div class="config-group">
        <label>NWS Fetch Interval (seconds)</label>
        <input type="text" value="60" readonly>
      </div>
      <div class="config-group">
        <label>County Fetch Interval (seconds)</label>
        <input type="text" value="120" readonly>
      </div>
      <div class="config-group">
        <label>All Florida Max Files</label>
        <input type="text" value="10" readonly>
      </div>
      <div class="config-group">
        <label>All Florida Max Age (hours)</label>
        <input type="text" value="24" readonly>
      </div>
    </div>
  </div>

  <!-- URLs tab (always last) -->
  <div id="tab-urls" class="tab-panel">
    <div class="card">
      <h2>Service URLs &amp; Endpoints</h2>
      <ul class="url-list">
        <li><span class="url-label">Dashboard</span>
          <a href="http://localhost:5000" target="_blank">http://localhost:5000</a></li>
        <li><span class="url-label">NWS FL Alerts API</span>
          <a href="https://api.weather.gov/alerts/active?area=FL" target="_blank">
            api.weather.gov/alerts/active?area=FL</a></li>
        <li><span class="url-label">Icecast Stream</span>
          <a href="http://localhost:8000/fpren" target="_blank">
            localhost:8000/fpren</a></li>
        <li><span class="url-label">MongoDB</span>
          <span>mongodb://localhost:27017/weather_rss</span></li>
        <li><span class="url-label">FL511 Traffic</span>
          <span>fl511.com API</span></li>
      </ul>
    </div>
  </div>

  <div id="toast"></div>

  <script>
    function switchTab(name) {
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      event.target.classList.add('active');
      if (name === 'alerts') loadAlerts();
      if (name === 'zones')  loadZones();
    }

    function showToast(msg, isError) {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.className = isError ? 'error' : '';
      t.style.display = 'block';
      setTimeout(() => { t.style.display = 'none'; }, 3000);
    }

    function loadStatus() {
      fetch('/api/status')
        .then(r => r.json())
        .then(d => {
          const s = document.getElementById('station-status');
          s.textContent = d.station;
          s.className   = 'badge ' + (d.station === 'Running' ? 'ok' : 'error');
          const m = document.getElementById('mongo-status');
          m.textContent = d.mongo;
          m.className   = 'badge ' + (d.mongo === 'Online' ? 'ok' : 'error');
          document.getElementById('fl-count').textContent      = d.fl_alerts;
          document.getElementById('alachua-count').textContent = d.alachua_alerts;
          document.getElementById('airport-count').textContent = d.airport_delays;
          document.getElementById('last-updated').textContent  =
            'Last updated: ' + new Date().toLocaleTimeString();
        })
        .catch(() => showToast('Status fetch failed', true));
    }

    function loadAlerts() {
      fetch('/api/status')
        .then(r => r.json())
        .then(d => {
          const tbody = document.getElementById('alerts-table');
          if (!d.recent_alerts || d.recent_alerts.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4">No active alerts</td></tr>';
          } else {
            tbody.innerHTML = d.recent_alerts.map(a => {
              const sev = (a.severity || '').toLowerCase();
              const cls = sev === 'extreme' ? 'sev-extreme'
                        : sev === 'severe'  ? 'sev-severe'
                        : sev === 'moderate' ? 'sev-moderate' : '';
              return '<tr>' +
                '<td>' + (a.event    || '') + '</td>' +
                '<td class="' + cls + '">' + (a.severity || '') + '</td>' +
                '<td>' + (a.area_desc || '').substring(0, 40) + '</td>' +
                '<td>' + (a.source   || '') + '</td>' +
                '</tr>';
            }).join('');
          }
        })
        .catch(() => showToast('Alerts fetch failed', true));
    }

    function loadZones() {
      fetch('/api/zones')
        .then(r => r.json())
        .then(d => {
          const tbody = document.getElementById('zones-table');
          if (!d.zones || d.zones.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4">No zones found</td></tr>';
            return;
          }
          tbody.innerHTML = d.zones.map(z => {
            const type    = z.catch_all ? 'Catch-All' : 'County';
            const counties = z.catch_all ? 'All Florida'
                           : (z.counties || []).slice(0, 4).join(', ')
                             + (z.counties.length > 4 ? '...' : '');
            const cleanup = z.cleanup
              ? (z.cleanup.max_age_hours + 'h' +
                 (z.cleanup.max_files ? ' / ' + z.cleanup.max_files + ' files' : ''))
              : '--';
            return '<tr>' +
              '<td>' + (z.zone_id || '') + '</td>' +
              '<td>' + type + '</td>' +
              '<td>' + counties + '</td>' +
              '<td>' + cleanup + '</td>' +
              '</tr>';
          }).join('');
        })
        .catch(() => showToast('Zones fetch failed', true));
    }

    function runCommand(cmd, btn) {
      if (!confirm('Run ' + cmd + '?')) return;
      btn.disabled = true;
      fetch('/api/command/' + cmd, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
          showToast(d.message || d.status, d.status === 'error');
          setTimeout(() => { btn.disabled = false; }, 5000);
        })
        .catch(() => {
          showToast('Command failed', true);
          btn.disabled = false;
        });
    }

    loadStatus();
    setInterval(loadStatus, 30000);
  </script>
</body>
</html>
"""


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/api/status")
def api_status():
    status = {
        "station":        "Running",
        "mongo":          "Offline",
        "fl_alerts":      0,
        "alachua_alerts": 0,
        "airport_delays": 0,
        "recent_alerts":  [],
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }
    try:
        from pymongo import MongoClient
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
        db     = client["weather_rss"]
        client.server_info()
        status["mongo"] = "Online"
        alerts = list(db["nws_alerts"].find(
            {}, {"event": 1, "severity": 1, "area_desc": 1, "source": 1,
                 "alachua_county": 1, "_id": 0}
        ).limit(20))
        status["fl_alerts"]      = len(alerts)
        status["alachua_alerts"] = sum(1 for a in alerts if a.get("alachua_county"))
        status["airport_delays"] = db["airport_delays"].count_documents({"has_delay": True})
        status["recent_alerts"]  = alerts[:10]
        client.close()
    except Exception as e:
        logger.warning("MongoDB unavailable: %s", e)
        status["mongo"]   = "Offline"
        status["station"] = "Degraded"
    return jsonify(status)


@app.route("/api/zones")
def api_zones():
    try:
        from pymongo import MongoClient
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
        db     = client["weather_rss"]
        zones  = list(db["zone_definitions"].find(
            {}, {"zone_id": 1, "catch_all": 1, "counties": 1, "cleanup": 1, "_id": 0}
        ))
        client.close()
        return jsonify({"zones": zones})
    except Exception as e:
        logger.warning("Zones fetch error: %s", e)
        return jsonify({"zones": []})


@app.route("/api/command/<cmd>", methods=["POST"])
def api_command(cmd):
    if cmd not in ALLOWED_COMMANDS:
        return jsonify({"status": "error", "message": f"Unknown command: {cmd}"}), 400
    command = ALLOWED_COMMANDS[cmd]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return jsonify({"status": "ok", "message": f"{cmd} completed successfully."})
        else:
            return jsonify({"status": "error",
                            "message": f"{cmd} failed: {result.stderr[:200]}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": f"{cmd} timed out."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

def run_dashboard(host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
    logger.info("Starting web dashboard on %s:%d", host, port)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard()
