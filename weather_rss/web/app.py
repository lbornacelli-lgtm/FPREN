import json
import os
import time as _time
import urllib.request as _ureq
from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory, url_for
from pymongo import MongoClient
from datetime import datetime, timezone

# -------------------- CONFIG --------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "weather_rss"
COLLECTION = "feed_status"

# ---- Stream zone config (shared with weather_station broadcast engine) ----
ZONE_OVERRIDES_FILE = "/home/lh_admin/weather_station/config/stream_zone_overrides.json"
SMTP_CFG_FILE       = "/home/lh_admin/weather_rss/config/smtp_config.json"

AVAILABLE_ZONES = [
    "all_florida", "north_florida", "central_florida", "south_florida",
    "miami", "jacksonville", "orlando", "tampa",
]

STREAMS = [
    {"id": "stream_8000", "label": "All Florida",     "port": 8000, "mount": "/beacon",          "zone": "all_florida"},
    {"id": "stream_8001", "label": "North Florida",   "port": 8001, "mount": "/north-florida",   "zone": "north_florida"},
    {"id": "stream_8002", "label": "Central Florida", "port": 8002, "mount": "/central-florida", "zone": "central_florida"},
    {"id": "stream_8003", "label": "South Florida",   "port": 8003, "mount": "/south-florida",   "zone": "south_florida"},
    {"id": "stream_8004", "label": "Miami",           "port": 8004, "mount": "/miami",           "zone": "miami"},
]

def _load_zone_overrides():
    try:
        with open(ZONE_OVERRIDES_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

def _load_smtp_cfg() -> dict:
    try:
        with open(SMTP_CFG_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"smtp_host": "smtp.gmail.com", "smtp_port": 587,
                "use_tls": True, "use_auth": True,
                "smtp_user": "", "smtp_pass": "", "mail_from": "", "mail_to": ""}

def _save_smtp_cfg(data: dict):
    os.makedirs(os.path.dirname(SMTP_CFG_FILE), exist_ok=True)
    with open(SMTP_CFG_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _save_zone_overrides(data):
    os.makedirs(os.path.dirname(ZONE_OVERRIDES_FILE), exist_ok=True)
    with open(ZONE_OVERRIDES_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---- Weather cities config ----
WEATHER_CITIES = [
    {"name": "Gainesville",  "icao": "KGNV", "lat": 29.6917, "lon": -82.2760},
    {"name": "Jacksonville", "icao": "KJAX", "lat": 30.4941, "lon": -81.6879},
    {"name": "Miami",        "icao": "KMIA", "lat": 25.7959, "lon": -80.2870},
    {"name": "Orlando",      "icao": "KMCO", "lat": 28.4294, "lon": -81.3089},
    {"name": "Tampa",        "icao": "KTPA", "lat": 27.9755, "lon": -82.5332},
]
_NWS_UA          = "BeaconWeatherStation/1.0 (lh_admin@localhost)"
_NWS_GRID_CACHE: dict = {}   # icao → nws forecast url
_WX_CACHE:       dict = {}   # icao → {"data": dict, "ts": float}
_WX_CACHE_TTL    = 600       # 10 minutes

def _nws_fetch(url: str) -> dict:
    req = _ureq.Request(url, headers={"User-Agent": _NWS_UA, "Accept": "application/geo+json"})
    with _ureq.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

def _get_city_forecast_url(city: dict):
    icao = city["icao"]
    if icao not in _NWS_GRID_CACHE:
        try:
            data = _nws_fetch(f"https://api.weather.gov/points/{city['lat']},{city['lon']}")
            _NWS_GRID_CACHE[icao] = data["properties"]["forecast"]
        except Exception:
            _NWS_GRID_CACHE[icao] = None
    return _NWS_GRID_CACHE.get(icao)

def _stream_list():
    overrides = _load_zone_overrides()
    return [{**s, "zone": overrides.get(s["id"], s["zone"])} for s in STREAMS]

STALE_THRESHOLD_MIN = 30  # feeds older than 30 minutes are considered stale
ALERTS_LIMIT = 50         # most recent NWS alerts to display
TRAFFIC_LIMIT = 200

# Severity → row colour
SEVERITY_CLASS = {
    "Extreme":  "sev-extreme",
    "Severe":   "sev-severe",
    "Moderate": "sev-moderate",
    "Minor":    "sev-minor",
}

FLTCAT_CLASS = {
    "LIFR": "flt-lifr",
    "IFR":  "flt-ifr",
    "MVFR": "flt-mvfr",
    "VFR":  "flt-vfr",
}

# -------------------- APP -----------------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
status_col       = db[COLLECTION]
alerts_col       = db["nws_alerts"]
airport_metar_col = db["airport_metar"]
fl_traffic_col   = db["fl_traffic"]
school_col       = db["school_closings"]

# -------------------- TEMPLATE ------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="60">
<link rel="icon" type="image/png" href="/static/fpren.png">
<title>Beacon Alerts Dashboard</title>
<style>
  body { font-family: Arial, sans-serif; margin: 0; background: #f5f5f5; }

  /* ---- Header bar ---- */
  .site-header {
    display:flex; align-items:center; background:#111; padding:10px 24px; gap:18px;
    border-bottom:3px solid #0077aa;
  }
  .site-header img { height:60px; width:auto; }
  .site-header-title { color:#fff; font-size:1.25rem; font-weight:700; letter-spacing:0.02em; }
  .site-header-sub   { color:#aaa; font-size:0.8rem; margin-top:2px; }

  /* ---- Tab nav ---- */
  .tab-nav { display:flex; background:#222; border-bottom:3px solid #0077aa; margin-bottom:0; }
  .tab-nav button {
    padding:12px 28px; border:none; background:none; color:#aaa;
    font-size:0.95rem; cursor:pointer; font-weight:600; transition:color .15s;
  }
  .tab-nav button:hover { color:#fff; }
  .tab-nav button.active { color:#00cfff; border-bottom:3px solid #00cfff; }
  .tab-panel { display:none; padding:20px; }
  .tab-panel.active { display:block; }

  /* ---- Config tab styles ---- */
  .cfg-card {
    background:#fff; border:1px solid #ddd; border-radius:6px;
    padding:18px; margin-bottom:18px; max-width:860px;
  }
  .cfg-card h2 { margin:0 0 14px; font-size:1rem; color:#0077aa; }
  .cfg-table { width:100%; border-collapse:collapse; font-size:0.9rem; }
  .cfg-table th {
    text-align:left; padding:9px 12px; background:#0077aa;
    color:#fff; font-weight:600;
  }
  .cfg-table td { padding:10px 12px; border-bottom:1px solid #eee; vertical-align:middle; }
  .cfg-table tr:last-child td { border-bottom:none; }
  .cfg-table tr:hover td { background:#f0f8ff; }
  .zone-select {
    border:1px solid #bbb; border-radius:4px; padding:5px 9px;
    font-size:0.875rem; background:#fff; min-width:180px; cursor:pointer;
  }
  .zone-select:focus { outline:none; border-color:#0077aa; }
  tr.zone-changed td { background:#fffbe6 !important; }
  .btn-save {
    padding:6px 14px; background:#7b1fa2; color:#fff; border:none;
    border-radius:4px; font-size:0.82rem; font-weight:700; cursor:pointer;
  }
  .btn-save:hover { background:#6a1b9a; }
  .port-tag {
    display:inline-block; background:#e0f4ff; color:#0077aa;
    border:1px solid #0077aa; border-radius:3px;
    padding:1px 7px; font-size:0.78rem; font-family:monospace;
  }
  .cfg-note { font-size:0.78rem; color:#888; margin-top:10px; font-style:italic; }

  /* ---- SMTP card ---- */
  .smtp-grid { display:grid; grid-template-columns:2fr 1fr 2fr 2fr; gap:10px 16px; align-items:end; }
  .smtp-grid2 { display:grid; grid-template-columns:2fr 2fr 1fr 1fr; gap:10px 16px; align-items:end; margin-top:10px; }
  .smtp-grid label, .smtp-grid2 label { display:flex; flex-direction:column; font-size:0.82rem;
    font-weight:600; color:#444; gap:4px; }
  .smtp-input {
    border:1px solid #bbb; border-radius:4px; padding:6px 9px;
    font-size:0.875rem; background:#fff; width:100%; box-sizing:border-box;
  }
  .smtp-input:focus { outline:none; border-color:#0077aa; }
  .smtp-check { display:flex; align-items:center; gap:8px; font-size:0.875rem;
    font-weight:600; color:#444; padding-bottom:4px; }
  .smtp-check input { width:16px; height:16px; cursor:pointer; }
  .smtp-btn-row { margin-top:14px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .btn-smtp-save { padding:7px 18px; background:#0077aa; color:#fff; border:none;
    border-radius:4px; font-size:0.875rem; font-weight:700; cursor:pointer; }
  .btn-smtp-save:hover { background:#005f8a; }
  .btn-smtp-test { padding:7px 18px; background:#2e7d32; color:#fff; border:none;
    border-radius:4px; font-size:0.875rem; font-weight:700; cursor:pointer; }
  .btn-smtp-test:hover { background:#1b5e20; }
  .smtp-status { font-size:0.8rem; font-style:italic; color:#666; }

  /* ---- Toast ---- */
  #toast {
    position:fixed; bottom:20px; right:20px; background:#0077aa; color:#fff;
    padding:11px 20px; border-radius:5px; font-weight:700; font-size:0.88rem;
    display:none; z-index:9999;
  }

  /* ---- Existing data styles ---- */
  h2   { margin: 24px 0 8px; }
  table { border-collapse: collapse; width: 100%; background: #fff; margin-bottom: 32px; }
  th, td { border: 1px solid #ccc; padding: 8px 10px; text-align: left; }
  th { background: #333; color: white; }
  td.center { text-align: center; }

  /* Feed status colours */
  .OK    { background-color: #d4f8d4; }
  .STALE { background-color: #fff3cd; }
  .ERROR { background-color: #f8d7da; }

  /* Alert severity colours */
  .sev-extreme  { background-color: #f8d7da; }
  .sev-severe   { background-color: #ffe5b4; }
  .sev-moderate { background-color: #fff3cd; }
  .sev-minor    { background-color: #d4f8d4; }

  /* Flight category colours */
  .flt-lifr { background-color: #f8d7da; color: #721c24; font-weight: bold; }
  .flt-ifr  { background-color: #ffe5b4; color: #856404; font-weight: bold; }
  .flt-mvfr { background-color: #cce5ff; color: #004085; font-weight: bold; }
  .flt-vfr  { background-color: #d4f8d4; color: #155724; font-weight: bold; }

  .badge { display:inline-block; padding:2px 8px; border-radius:4px;
           font-size:0.8rem; font-weight:bold; }
  .badge-yes { background:#d4f8d4; color:#155724; }
  .badge-no  { background:#f8d7da; color:#721c24; }

  .feedback-btn { float: right; padding: 6px 14px; background: #555; color: #fff;
                  border: none; border-radius: 4px; cursor: pointer; font-size: 0.9rem; }
  .feedback-btn:hover { background: #333; }
  dialog { border: 1px solid #999; border-radius: 6px; padding: 24px; min-width: 340px; }
  dialog::backdrop { background: rgba(0,0,0,0.4); }
  dialog label { display: block; margin-top: 12px; font-weight: bold; }
  dialog input, dialog textarea { width: 100%; box-sizing: border-box; padding: 6px;
                                   margin-top: 4px; border: 1px solid #ccc; border-radius: 4px; }
  dialog textarea { resize: vertical; height: 100px; }
  .dialog-actions { margin-top: 16px; display: flex; justify-content: flex-end; gap: 8px; }
  .btn-primary   { background: #0d6efd; color: #fff; border: none; padding: 6px 14px;
                   border-radius: 4px; cursor: pointer; }
  .btn-secondary { background: #6c757d; color: #fff; border: none; padding: 6px 14px;
                   border-radius: 4px; cursor: pointer; }
  small { color: #888; }
  .no-data { text-align:center; color:#888; font-style:italic; }

  /* ---- Weather tab ---- */
  .wx-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:20px; max-width:1100px; padding:4px 0; }
  .wx-card { background:#fff; border:1px solid #ddd; border-radius:8px; overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,.07); }
  .wx-card-hdr { background:#0077aa; color:#fff; padding:10px 16px; font-size:1.05rem; font-weight:700; display:flex; justify-content:space-between; align-items:center; }
  .wx-card-hdr small { font-size:0.78rem; font-weight:400; opacity:.8; }
  .wx-current { display:flex; align-items:center; gap:16px; padding:12px 16px; border-bottom:1px solid #eee; background:#fafcff; }
  .wx-temp { font-size:2.4rem; font-weight:700; color:#0077aa; min-width:86px; }
  .wx-cur-det { font-size:0.84rem; color:#444; line-height:1.8; }
  .wx-flt { display:inline-block; padding:2px 7px; border-radius:3px; font-size:0.78rem; font-weight:700; }
  .wx-vfr  { background:#d4f8d4; color:#155724; }
  .wx-mvfr { background:#cce5ff; color:#004085; }
  .wx-ifr  { background:#ffe5b4; color:#856404; }
  .wx-lifr { background:#f8d7da; color:#721c24; }
  .wx-fc-wrap { padding:10px 16px 14px; }
  .wx-fc-title { font-size:0.78rem; font-weight:700; color:#888; text-transform:uppercase; letter-spacing:.04em; margin-bottom:8px; }
  .wx-periods { display:flex; gap:7px; overflow-x:auto; padding-bottom:4px; }
  .wx-period { min-width:96px; background:#f4f8fc; border:1px solid #dde6f0; border-radius:6px; padding:8px 9px; flex-shrink:0; }
  .wx-p-name { font-size:0.72rem; font-weight:700; color:#0077aa; margin-bottom:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .wx-p-temp { font-size:1.15rem; font-weight:700; color:#222; }
  .wx-p-desc { font-size:0.7rem; color:#555; margin-top:3px; line-height:1.3; }
  .wx-p-rain { font-size:0.68rem; color:#1565c0; margin-top:3px; font-weight:600; }
</style>
</head>
<body>

<!-- Header with logo -->
<header class="site-header">
  <img src="/static/fpren.png" alt="Beacon Logo">
  <div>
    <div class="site-header-title">Beacon Alerts Dashboard</div>
    <div class="site-header-sub">Weather &bull; Traffic &bull; Alerts &bull; Icecast</div>
  </div>
</header>

<!-- Tab navigation -->
<nav class="tab-nav">
  <button class="active" onclick="showTab('config',this)">Config</button>
  <button onclick="showTab('weather',this);loadWeather()">Weather</button>
  <button onclick="showTab('data',this)">Alerts &amp; Data</button>
</nav>

<!-- ===== CONFIG TAB ===== -->
<div id="tab-config" class="tab-panel active">
  <div class="cfg-card">
    <h2>Stream Zone Configuration</h2>
    <table class="cfg-table">
      <thead>
        <tr><th>Stream</th><th>Port</th><th>Mount</th><th>Assigned Zone</th><th>Action</th></tr>
      </thead>
      <tbody id="cfg-rows">
        <tr><td colspan="5" style="color:#aaa;text-align:center;padding:18px;">Loading...</td></tr>
      </tbody>
    </table>
    <p class="cfg-note">Zone changes persist across restarts and are picked up immediately by the broadcast engine.</p>
  </div>

  <!-- SMTP Settings card -->
  <div class="cfg-card">
    <h2>Email / SMTP Settings</h2>

    <div class="smtp-grid">
      <label>SMTP Host
        <input id="smtp-host" class="smtp-input" type="text" placeholder="smtp.gmail.com">
      </label>
      <label>Port
        <select id="smtp-port" class="smtp-input">
          <option>25</option><option>465</option><option selected>587</option><option>2525</option>
        </select>
      </label>
      <label>From Address
        <input id="smtp-from" class="smtp-input" type="email" placeholder="alerts@yourstation.com">
      </label>
      <label>To Address(es)
        <input id="smtp-to" class="smtp-input" type="text" placeholder="you@example.com">
      </label>
    </div>

    <div class="smtp-grid2">
      <label>Username
        <input id="smtp-user" class="smtp-input" type="text" autocomplete="off" placeholder="user@gmail.com">
      </label>
      <label>Password
        <input id="smtp-pass" class="smtp-input" type="password" autocomplete="new-password" placeholder="••••••••">
      </label>
      <div class="smtp-check">
        <input id="smtp-tls" type="checkbox" checked>
        <span>STARTTLS</span>
      </div>
      <div class="smtp-check">
        <input id="smtp-auth" type="checkbox" checked>
        <span>Authentication</span>
      </div>
    </div>

    <div class="smtp-btn-row">
      <button class="btn-smtp-save" onclick="saveSmtp()">Save SMTP Settings</button>
      <button class="btn-smtp-test" onclick="testSmtp()">Send Test Email</button>
      <span id="smtp-status" class="smtp-status"></span>
    </div>
    <p class="cfg-note">Settings are saved to config/smtp_config.json and used by all email alert notifications.</p>
  </div>
</div>

<!-- ===== WEATHER TAB ===== -->
<div id="tab-weather" class="tab-panel">
  <div id="wx-load" style="text-align:center;padding:40px;color:#888;font-size:1rem;">Click the Weather tab to load forecast data&hellip;</div>
  <div id="wx-grid" class="wx-grid"></div>
</div>

<!-- ===== DATA TAB ===== -->
<div id="tab-data" class="tab-panel">

<div style="display:flex; justify-content:flex-end; padding:0 0 4px;">
  <button class="feedback-btn" onclick="document.getElementById('feedbackDialog').showModal()">Feedback</button>
</div>
<small>Auto-refreshes every 60 seconds &mdash; {{ now }}</small>

{% if now_playing %}
<p style="margin:8px 0; font-size:0.95rem;">
  <strong>&#9654; Now Playing:</strong>
  {{ now_playing.title }}
  <span style="color:#666;">[{{ now_playing.category }}]</span>
  &mdash; <small>started {{ now_playing.started_at }}</small>
</p>
{% endif %}

{% if request.args.get('msg') %}
<p style="color:green; font-weight:bold;">{{ request.args.get('msg') }}</p>
{% endif %}

<dialog id="feedbackDialog">
  <h3 style="margin-top:0">Send Feedback</h3>
  <form method="post" action="/feedback">
    <label>Name (optional)
      <input type="text" name="name" placeholder="Your name">
    </label>
    <label>Message <span style="color:red">*</span>
      <textarea name="message" placeholder="Share your feedback..." required></textarea>
    </label>
    <div class="dialog-actions">
      <button type="button" class="btn-secondary"
              onclick="document.getElementById('feedbackDialog').close()">Cancel</button>
      <button type="submit" class="btn-primary">Submit</button>
    </div>
  </form>
</dialog>

<!-- ===== NWS ALERTS ===== -->
<h2>NWS Alerts <small>({{ alerts|length }} most recent)</small></h2>
<table>
  <tr>
    <th>Event</th>
    <th>Headline</th>
    <th>Severity</th>
    <th>Areas</th>
    <th>Sender</th>
    <th>Sent</th>
    <th>WAV</th>
  </tr>
  {% for a in alerts %}
  <tr class="{{ a.sev_class }}">
    <td><strong>{{ a.event }}</strong></td>
    <td>{{ a.headline }}</td>
    <td class="center">{{ a.severity }}</td>
    <td>{{ a.area_desc }}</td>
    <td>{{ a.sender }}</td>
    <td class="center">{{ a.sent }}</td>
    <td class="center">
      {% if a.tts_generated %}
        <span class="badge badge-yes">&#10003; WAV</span>
      {% else %}
        <span class="badge badge-no">Pending</span>
      {% endif %}
    </td>
  </tr>
  {% else %}
  <tr><td colspan="7" class="no-data">No alerts in database</td></tr>
  {% endfor %}
</table>

<!-- ===== AIRPORT WEATHER ===== -->
<h2>Airport Weather <small>(METAR — {{ airports|length }} stations)</small></h2>
<table>
  <tr>
    <th>ICAO</th>
    <th>Airport</th>
    <th>Cat</th>
    <th>Temp °F</th>
    <th>Temp °C</th>
    <th>Dewp °F</th>
    <th>Dewp °C</th>
    <th>Wind Dir</th>
    <th>Wind kt</th>
    <th>Vis</th>
    <th>Raw METAR</th>
    <th>Obs Time (UTC)</th>
  </tr>
  {% for ap in airports %}
  <tr>
    <td><strong>{{ ap.icaoId }}</strong></td>
    <td>{{ ap.name }}</td>
    <td class="center {{ ap.flt_class }}">{{ ap.fltCat }}</td>
    <td class="center">{{ ap.temp_f }}</td>
    <td class="center">{{ ap.temp }}</td>
    <td class="center">{{ ap.dewp_f }}</td>
    <td class="center">{{ ap.dewp }}</td>
    <td class="center">{{ ap.wdir }}</td>
    <td class="center">{{ ap.wspd }}</td>
    <td class="center">{{ ap.visib }}</td>
    <td><small>{{ ap.rawOb }}</small></td>
    <td class="center">{{ ap.obsTime }}</td>
  </tr>
  {% else %}
  <tr><td colspan="12" class="no-data">No METAR data</td></tr>
  {% endfor %}
</table>

<!-- ===== FL TRAFFIC ===== -->
<h2>FL Traffic <small>({{ traffic|length }} active incidents)</small></h2>
<table>
  <tr>
    <th>Type</th>
    <th>Road</th>
    <th>Location</th>
    <th>County</th>
    <th>Severity</th>
    <th>Last Updated</th>
  </tr>
  {% for t in traffic %}
  <tr>
    <td>{{ t.type }}</td>
    <td>{{ t.road }}</td>
    <td>{{ t.location }}</td>
    <td>{{ t.county }}</td>
    <td class="center">{{ t.severity }}</td>
    <td class="center">{{ t.last_updated }}</td>
  </tr>
  {% else %}
  <tr><td colspan="6" class="no-data">No traffic incidents</td></tr>
  {% endfor %}
</table>

<!-- ===== SCHOOL CLOSINGS ===== -->
<h2>School Closings &amp; Delays <small>(Alachua County)</small></h2>
<table>
  <tr>
    <th>Title</th>
    <th>Type</th>
    <th>Published</th>
    <th>Fetched</th>
  </tr>
  {% for s in school %}
  <tr class="sev-moderate">
    <td>{{ s.title }}</td>
    <td class="center">{{ s.closure_type }}</td>
    <td class="center">{{ s.published_date }}</td>
    <td class="center">{{ s.fetched_at }}</td>
  </tr>
  {% else %}
  <tr><td colspan="4" class="no-data">No active school closings or delays</td></tr>
  {% endfor %}
</table>

<!-- ===== RSS FEED STATUS ===== -->
<h2>RSS Feed Status</h2>
<table>
  <tr>
    <th>Feed Filename</th>
    <th>Last Success</th>
    <th>Age (min)</th>
    <th>File Size (KB)</th>
    <th>Status</th>
  </tr>
  {% for feed in feeds %}
  <tr class="{{ feed.row_class }}">
    <td>{{ feed.filename }}</td>
    <td class="center">{{ feed.last_success or "—" }}</td>
    <td class="center">{{ feed.age_min or "—" }}</td>
    <td class="center">{{ feed.file_size_kb or "—" }}</td>
    <td class="center">{{ feed.status }}</td>
  </tr>
  {% else %}
  <tr><td colspan="5" class="no-data">No feed status data</td></tr>
  {% endfor %}
</table>

</div><!-- end tab-data -->

<div id="toast"></div>

<script>
const ZONES = {{ zones | tojson }};

function showTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  localStorage.setItem('activeTab', name);
}

function toast(msg, ok=true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = ok ? '#0077aa' : '#c0392b';
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 2800);
}

function buildZoneOptions(selected) {
  return ZONES.map(z =>
    `<option value="${z}" ${z===selected?'selected':''}>${z.replace(/_/g,' ')}</option>`
  ).join('');
}

function loadConfig() {
  fetch('/api/streams')
    .then(r => r.json())
    .then(streams => {
      const tbody = document.getElementById('cfg-rows');
      tbody.innerHTML = '';
      streams.forEach(s => {
        const row = document.createElement('tr');
        row.id = 'cfg-row-' + s.id;
        row.innerHTML = `
          <td><strong>${s.label}</strong></td>
          <td><span class="port-tag">:${s.port}</span></td>
          <td><code>${s.mount}</code></td>
          <td>
            <select class="zone-select" id="zone-sel-${s.id}"
                    onchange="document.getElementById('cfg-row-${s.id}').classList.add('zone-changed')">
              ${buildZoneOptions(s.zone)}
            </select>
          </td>
          <td><button class="btn-save" onclick="saveZone('${s.id}')">Save</button></td>`;
        tbody.appendChild(row);
      });
    });
}

function saveZone(streamId) {
  const zone = document.getElementById('zone-sel-' + streamId).value;
  fetch('/api/streams/' + streamId + '/zone', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({zone})
  })
  .then(r => r.json())
  .then(d => {
    if (d.ok) {
      toast('Saved: ' + zone.replace(/_/g,' '));
      document.getElementById('cfg-row-' + streamId).classList.remove('zone-changed');
    } else {
      toast(d.message || 'Save failed', false);
    }
  })
  .catch(() => toast('Request failed', false));
}

loadConfig();
loadSmtp();

// Restore last active tab after page reload
(function() {
  const saved = localStorage.getItem('activeTab');
  if (!saved || saved === 'config') return;
  const panel = document.getElementById('tab-' + saved);
  if (!panel) return;
  for (const btn of document.querySelectorAll('.tab-nav button')) {
    if ((btn.getAttribute('onclick') || '').includes("'" + saved + "'")) {
      showTab(saved, btn);
      if (saved === 'weather') loadWeather();
      break;
    }
  }
})();

// ---- SMTP ----
function loadSmtp() {
  fetch('/api/smtp')
    .then(r => r.json())
    .then(cfg => {
      document.getElementById('smtp-host').value = cfg.smtp_host || '';
      const portSel = document.getElementById('smtp-port');
      portSel.value = String(cfg.smtp_port || 587);
      if (!portSel.value) portSel.value = '587';
      document.getElementById('smtp-from').value  = cfg.mail_from || '';
      document.getElementById('smtp-to').value    = cfg.mail_to   || '';
      document.getElementById('smtp-user').value  = cfg.smtp_user || '';
      document.getElementById('smtp-pass').value  = cfg.smtp_pass || '';
      document.getElementById('smtp-tls').checked  = !!cfg.use_tls;
      document.getElementById('smtp-auth').checked = !!cfg.use_auth;
    })
    .catch(() => setSmtpStatus('Could not load SMTP settings', false));
}

function smtpPayload() {
  return {
    smtp_host: document.getElementById('smtp-host').value.trim(),
    smtp_port: parseInt(document.getElementById('smtp-port').value) || 587,
    mail_from: document.getElementById('smtp-from').value.trim(),
    mail_to:   document.getElementById('smtp-to').value.trim(),
    smtp_user: document.getElementById('smtp-user').value.trim(),
    smtp_pass: document.getElementById('smtp-pass').value,
    use_tls:   document.getElementById('smtp-tls').checked,
    use_auth:  document.getElementById('smtp-auth').checked,
  };
}

function saveSmtp() {
  setSmtpStatus('Saving…');
  fetch('/api/smtp', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(smtpPayload())})
    .then(r => r.json())
    .then(d => setSmtpStatus(d.message, d.ok))
    .catch(() => setSmtpStatus('Save failed', false));
}

function testSmtp() {
  setSmtpStatus('Sending test email…');
  fetch('/api/smtp/test', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(smtpPayload())})
    .then(r => r.json())
    .then(d => setSmtpStatus(d.message, d.ok))
    .catch(() => setSmtpStatus('Test failed', false));
}

function setSmtpStatus(msg, ok=true) {
  const el = document.getElementById('smtp-status');
  el.textContent = msg;
  el.style.color = ok ? '#2e7d32' : '#c0392b';
}

// ---- Weather ----
let _wxLoaded = false;
function loadWeather() {
  if (_wxLoaded) return;
  _wxLoaded = true;
  document.getElementById('wx-load').textContent = 'Loading weather\u2026';
  fetch('/api/weather')
    .then(r => r.json())
    .then(cities => {
      const grid = document.getElementById('wx-grid');
      document.getElementById('wx-load').style.display = 'none';
      const fltCls = {VFR:'wx-vfr', MVFR:'wx-mvfr', IFR:'wx-ifr', LIFR:'wx-lifr'};
      grid.innerHTML = cities.map(city => {
        const c = city.current || {};
        const flt = c.flt_cat || '';
        const badge = flt ? `<span class="wx-flt ${fltCls[flt]||''}">${flt}</span>` : '';
        const obs   = c.obs_time ? `<span style="color:#bbb;font-size:0.72rem">Obs ${c.obs_time}</span>` : '';
        const windStr = (c.wind_dir != null && c.wind_spd != null)
          ? `${c.wind_dir}&deg; &commat; ${c.wind_spd} kt` : '&mdash;';
        const periods = (city.forecast || []).map(p => {
          const rain = p.precip_pct != null
            ? `<div class="wx-p-rain">&#128166; ${p.precip_pct}% rain</div>` : '';
          return `<div class="wx-period">
            <div class="wx-p-name">${p.name}</div>
            <div class="wx-p-temp">${p.temp}&deg;${p.temp_unit}</div>
            <div class="wx-p-desc">${p.short_forecast}</div>
            ${rain}
          </div>`;
        }).join('');
        return `<div class="wx-card">
          <div class="wx-card-hdr">${city.name}, FL <small>${city.icao}</small></div>
          <div class="wx-current">
            <div class="wx-temp">${c.temp_f != null ? c.temp_f + '&deg;F' : '&mdash;'}</div>
            <div class="wx-cur-det">
              Wind: ${windStr}<br>
              Visibility: ${c.visib ? c.visib + ' mi' : '&mdash;'}<br>
              ${badge} ${obs}
            </div>
          </div>
          <div class="wx-fc-wrap">
            <div class="wx-fc-title">7-Day Forecast</div>
            <div class="wx-periods">${periods || '<span style="color:#aaa">No forecast data</span>'}</div>
          </div>
        </div>`;
      }).join('');
    })
    .catch(() => {
      document.getElementById('wx-load').textContent = 'Could not load weather data.';
      _wxLoaded = false;
    });
}
</script>
</body>
</html>
"""

# -------------------- ROUTES ----------------------
@app.route("/")
def dashboard():
    now = datetime.now(timezone.utc)

    # --- RSS feed status ---
    feeds = []
    for feed in status_col.find():
        last_success = feed.get("last_success")
        age_min = None
        row_class = "OK"

        if last_success:
            if isinstance(last_success, str):
                last_success = datetime.fromisoformat(last_success)
            if last_success.tzinfo is None:
                last_success = last_success.replace(tzinfo=timezone.utc)
            age_min = round((now - last_success).total_seconds() / 60, 1)

        status = feed.get("status", "UNKNOWN")
        if status == "OK" and age_min and age_min > STALE_THRESHOLD_MIN:
            row_class = "STALE"
        elif status == "ERROR":
            row_class = "ERROR"

        feeds.append({
            "filename":     feed.get("filename", "—"),
            "last_success": last_success.strftime("%Y-%m-%d %H:%M:%S") if last_success else None,
            "age_min":      age_min,
            "file_size_kb": feed.get("file_size_kb", "—"),
            "status":       status,
            "row_class":    row_class,
        })

    # --- NWS alerts ---
    alerts = []
    for a in alerts_col.find({}, sort=[("fetched_at", -1)], limit=ALERTS_LIMIT):
        sent = a.get("sent", "")
        if isinstance(sent, datetime):
            sent = sent.strftime("%Y-%m-%d %H:%M")
        elif isinstance(sent, str) and sent:
            try:
                sent = datetime.fromisoformat(sent).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass

        severity = a.get("severity", "")
        alerts.append({
            "event":         a.get("event", "—"),
            "headline":      a.get("headline", "—"),
            "severity":      severity,
            "area_desc":     a.get("area_desc", "—"),
            "sender":        a.get("sender", "—"),
            "sent":          sent or "—",
            "tts_generated": a.get("tts_generated", False),
            "sev_class":     SEVERITY_CLASS.get(severity, ""),
        })

    # --- Airport METAR ---
    airports = []
    for ap in airport_metar_col.find({}, sort=[("icaoId", 1)]):
        flt_cat = ap.get("fltCat", "")
        obs = ap.get("obsTime", "")
        # Trim to compact display: 2026-03-04T01:53:00+00:00 → 03-04 01:53Z
        if isinstance(obs, str) and "T" in obs:
            try:
                dt = datetime.fromisoformat(obs)
                obs = dt.strftime("%m-%d %H:%MZ")
            except ValueError:
                pass
        def to_f(c):
            try:
                return round(float(c) * 9 / 5 + 32, 1)
            except (TypeError, ValueError):
                return ""

        temp_c = ap.get("temp", "")
        dewp_c = ap.get("dewp", "")
        airports.append({
            "icaoId":    ap.get("icaoId", ""),
            "name":      ap.get("name", ""),
            "fltCat":    flt_cat,
            "flt_class": FLTCAT_CLASS.get(flt_cat, ""),
            "temp":      temp_c,
            "temp_f":    to_f(temp_c),
            "dewp":      dewp_c,
            "dewp_f":    to_f(dewp_c),
            "wdir":      ap.get("wdir", ""),
            "wspd":      ap.get("wspd", ""),
            "visib":     ap.get("visib", ""),
            "rawOb":     ap.get("rawOb", ""),
            "obsTime":   obs,
        })

    # --- FL Traffic ---
    traffic = []
    for t in fl_traffic_col.find({}, sort=[("severity", 1)], limit=TRAFFIC_LIMIT):
        traffic.append({
            "type":         t.get("type", ""),
            "road":         t.get("road", ""),
            "location":     t.get("location", "") or "",
            "county":       t.get("county", ""),
            "severity":     t.get("severity", ""),
            "last_updated": t.get("last_updated", ""),
        })

    # --- School closings ---
    school = []
    for s in school_col.find({}, sort=[("fetched_at", -1)]):
        fetched = s.get("fetched_at", "")
        if isinstance(fetched, datetime):
            fetched = fetched.strftime("%Y-%m-%d %H:%M UTC")
        school.append({
            "title":          s.get("title", ""),
            "closure_type":   s.get("closure_type", ""),
            "published_date": s.get("published_date", ""),
            "fetched_at":     fetched,
        })

    # --- Now Playing ---
    now_playing = None
    try:
        with open("/tmp/beacon_now_playing.json") as f:
            now_playing = json.load(f)
    except (FileNotFoundError, ValueError):
        pass

    return render_template_string(
        HTML_TEMPLATE,
        feeds=feeds,
        alerts=alerts,
        airports=airports,
        traffic=traffic,
        school=school,
        now=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        now_playing=now_playing,
        zones=AVAILABLE_ZONES,
    )

# -------------------- STREAM CONFIG API ------------------
@app.route("/api/streams")
def api_streams():
    return jsonify(_stream_list())

@app.route("/api/streams/<stream_id>/zone", methods=["POST"])
def api_stream_zone(stream_id):
    data = request.get_json(silent=True) or {}
    zone = data.get("zone", "").strip()
    if not zone or zone not in AVAILABLE_ZONES:
        return jsonify({"ok": False, "message": "Invalid zone"}), 400
    if not any(s["id"] == stream_id for s in STREAMS):
        return jsonify({"ok": False, "message": "Unknown stream"}), 404
    overrides = _load_zone_overrides()
    overrides[stream_id] = zone
    _save_zone_overrides(overrides)
    return jsonify({"ok": True, "message": f"Zone set to {zone}"})

# -------------------- SMTP CONFIG API ------------------
@app.route("/api/smtp", methods=["GET"])
def api_smtp_get():
    return jsonify(_load_smtp_cfg())

@app.route("/api/smtp", methods=["POST"])
def api_smtp_save():
    data = request.get_json(silent=True) or {}
    cfg = {
        "smtp_host": str(data.get("smtp_host", "")).strip(),
        "smtp_port": int(data.get("smtp_port", 587)),
        "use_tls":   bool(data.get("use_tls", True)),
        "use_auth":  bool(data.get("use_auth", True)),
        "smtp_user": str(data.get("smtp_user", "")).strip(),
        "smtp_pass": str(data.get("smtp_pass", "")),
        "mail_from": str(data.get("mail_from", "")).strip(),
        "mail_to":   str(data.get("mail_to", "")).strip(),
    }
    try:
        _save_smtp_cfg(cfg)
        return jsonify({"ok": True, "message": f"Saved — {cfg['smtp_host']}:{cfg['smtp_port']}"})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500

@app.route("/api/smtp/test", methods=["POST"])
def api_smtp_test():
    import smtplib, threading
    from email.message import EmailMessage

    data = request.get_json(silent=True) or {}
    host      = str(data.get("smtp_host", "")).strip()
    port      = int(data.get("smtp_port", 587))
    use_tls   = bool(data.get("use_tls", True))
    use_auth  = bool(data.get("use_auth", True))
    user      = str(data.get("smtp_user", "")).strip()
    passwd    = str(data.get("smtp_pass", ""))
    mail_from = str(data.get("mail_from", "")).strip() or user
    mail_to   = str(data.get("mail_to", "")).strip()

    if not host:
        return jsonify({"ok": False, "message": "No SMTP host configured"}), 400
    if not mail_to:
        return jsonify({"ok": False, "message": "No recipient address configured"}), 400

    try:
        msg = EmailMessage()
        msg["Subject"] = "Beacon Dashboard — SMTP Test"
        msg["From"]    = mail_from
        msg["To"]      = mail_to
        msg.set_content(
            "This is a test email from the Beacon Alerts Dashboard.\n"
            "If you received this, your SMTP settings are working correctly."
        )
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            if use_auth and user and passwd:
                smtp.login(user, passwd)
            smtp.send_message(msg)
        return jsonify({"ok": True, "message": f"Test email sent to {mail_to}"})
    except Exception as exc:
        return jsonify({"ok": False, "message": f"SMTP error: {exc}"}), 500

# -------------------- WEATHER API ---------------
@app.route("/api/weather")
def api_weather():
    now_ts = _time.time()
    result = []
    for city in WEATHER_CITIES:
        icao = city["icao"]
        cached = _WX_CACHE.get(icao)
        if cached and (now_ts - cached["ts"]) < _WX_CACHE_TTL:
            result.append(cached["data"])
            continue

        city_data = {"name": city["name"], "icao": icao, "current": None, "forecast": []}

        # Current conditions from MongoDB METAR
        metar = airport_metar_col.find_one({"icaoId": icao})
        if metar:
            def _to_f(c):
                try:    return round(float(c) * 9 / 5 + 32, 1)
                except: return None
            obs = metar.get("obsTime", "")
            if isinstance(obs, str) and "T" in obs:
                try:
                    dt = datetime.fromisoformat(obs)
                    obs = dt.strftime("%m-%d %H:%MZ")
                except ValueError:
                    pass
            city_data["current"] = {
                "temp_f":   _to_f(metar.get("temp")),
                "wind_dir": metar.get("wdir"),
                "wind_spd": metar.get("wspd"),
                "visib":    metar.get("visib"),
                "flt_cat":  metar.get("fltCat", ""),
                "obs_time": obs,
            }

        # 7-day forecast from NWS
        try:
            fc_url = _get_city_forecast_url(city)
            if fc_url:
                fc_data = _nws_fetch(fc_url)
                city_data["forecast"] = [
                    {
                        "name":           p.get("name", ""),
                        "temp":           p.get("temperature"),
                        "temp_unit":      p.get("temperatureUnit", "F"),
                        "wind_speed":     p.get("windSpeed", ""),
                        "short_forecast": p.get("shortForecast", ""),
                        "precip_pct":     (p.get("probabilityOfPrecipitation") or {}).get("value"),
                    }
                    for p in fc_data["properties"]["periods"][:7]
                ]
        except Exception:
            pass

        _WX_CACHE[icao] = {"data": city_data, "ts": now_ts}
        result.append(city_data)

    return jsonify(result)

# -------------------- FEEDBACK ------------------
@app.route("/feedback", methods=["POST"])
def submit_feedback():
    name    = request.form.get("name", "").strip()
    message = request.form.get("message", "").strip()
    if not message:
        return redirect(url_for("dashboard") + "?msg=Feedback+message+is+required")
    db["feedback"].insert_one({
        "name":         name or "Anonymous",
        "message":      message,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    })
    return redirect(url_for("dashboard") + "?msg=Thank+you+for+your+feedback!")


# -------------------- RUN ------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
