import json
import os
import re
import time as _time
import urllib.request as _ureq
from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_file, send_from_directory, url_for
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta

# -------------------- CONFIG --------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "weather_rss"
COLLECTION = "feed_status"

# ---- Stream zone config (shared with weather_station broadcast engine) ----
ZONE_OVERRIDES_FILE = "/home/ufuser/Fpren-main/weather_station/config/stream_zone_overrides.json"
SMTP_CFG_FILE       = "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"

AVAILABLE_ZONES = [
    "all_florida", "north_florida", "central_florida", "south_florida",
    "miami", "jacksonville", "orlando", "tampa",
]

STREAMS = [
    {"id": "stream_8000", "label": "All Florida",     "port": 8000, "mount": "/fpren",           "zone": "all_florida"},
    {"id": "stream_8001", "label": "North Florida",   "port": 8001, "mount": "/north-florida",   "zone": "north_florida"},
    {"id": "stream_8002", "label": "Central Florida", "port": 8002, "mount": "/central-florida", "zone": "central_florida"},
    {"id": "stream_8003", "label": "South Florida",   "port": 8003, "mount": "/south-florida",   "zone": "south_florida"},
    {"id": "stream_8004", "label": "Miami",           "port": 8004, "mount": "/miami",           "zone": "miami"},
]

# ---- Dashboard state (shared between web + desktop via MongoDB) ----
def _get_dash_state():
    """Read the singleton dashboard_state document from MongoDB."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000)
        doc = client["weather_rss"]["dashboard_state"].find_one({"_id": "singleton"}) or {}
        client.close()
        return doc
    except Exception:
        return {}

def _set_dash_state(updates):
    """Upsert the singleton dashboard_state document."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000)
        client["weather_rss"]["dashboard_state"].update_one(
            {"_id": "singleton"},
            {"$set": {**updates, "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        client.close()
        return True
    except Exception:
        return False


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

# ---- Stream alert email ----
def _send_stream_alert_email(subject: str, body: str) -> tuple[bool, str]:
    import smtplib
    from email.message import EmailMessage
    cfg = _load_smtp_cfg()
    host      = cfg.get("smtp_host", "").strip()
    port      = int(cfg.get("smtp_port", 587))
    use_tls   = bool(cfg.get("use_tls", True))
    use_auth  = bool(cfg.get("use_auth", True))
    user      = cfg.get("smtp_user", "").strip()
    passwd    = cfg.get("smtp_pass", "")
    mail_from = cfg.get("mail_from", "").strip() or user
    mail_to   = cfg.get("mail_to", "").strip()
    if not host:
        return False, "No SMTP host configured"
    if not mail_to:
        return False, "No recipient configured"
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"]    = mail_from
        msg["To"]      = mail_to
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            if use_auth and user and passwd:
                smtp.login(user, passwd)
            smtp.send_message(msg)
        return True, f"Alert sent to {mail_to}"
    except Exception as exc:
        return False, str(exc)

# ---- Port 8000 stream monitor ----
import threading as _threading

_MONITOR_STREAM = STREAMS[0]   # port 8000, All Florida
_monitor_state  = {"live": None}  # None = unknown, True/False = last known state

def _check_stream_8000_live() -> bool:
    import xml.etree.ElementTree as ET, base64
    s = _MONITOR_STREAM
    try:
        req = _ureq.Request(f"http://localhost:{s['port']}/admin/stats")
        req.add_header("Authorization",
                       "Basic " + base64.b64encode(b"admin:hackme").decode())
        with _ureq.urlopen(req, timeout=5) as resp:
            tree = ET.fromstring(resp.read())
        for src in tree.findall(".//source"):
            if src.get("mount") == s["mount"]:
                return True
        return False
    except Exception:
        return False

def _stream_monitor_loop():
    while True:
        _time.sleep(30)
        live = _check_stream_8000_live()
        prev = _monitor_state["live"]
        _monitor_state["live"] = live
        if prev is True and not live:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            _send_stream_alert_email(
                subject=f"FPREN Stream Alert — Port 8000 Offline ({now})",
                body=(
                    f"Stream port 8000 (All Florida / {_MONITOR_STREAM['mount']}) "
                    f"has stopped streaming.\n\n"
                    f"Detected at: {now}\n"
                    f"Dashboard: http://10.242.41.77:5000\n"
                ),
            )

_monitor_thread = _threading.Thread(target=_stream_monitor_loop, daemon=True)
_monitor_thread.start()

# ---- Weather cities config ----
WEATHER_CITIES = [
    {"name": "Gainesville",  "icao": "KGNV", "lat": 29.6917, "lon": -82.2760},
    {"name": "Jacksonville", "icao": "KJAX", "lat": 30.4941, "lon": -81.6879},
    {"name": "Miami",        "icao": "KMIA", "lat": 25.7959, "lon": -80.2870},
    {"name": "Orlando",      "icao": "KMCO", "lat": 28.4294, "lon": -81.3089},
    {"name": "Tampa",        "icao": "KTPA", "lat": 27.9755, "lon": -82.5332},
]
_NWS_UA          = "FPRENWeatherStation/1.0 (ufuser@localhost)"
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

import bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

login_manager = LoginManager()
login_manager.login_view = 'login_page'
login_manager.login_message = 'Please log in to access FPREN.'

class User(UserMixin):
    def __init__(self, doc):
        self.id       = str(doc['_id'])
        self.username = doc['username']
        self.role     = doc.get('role', 'viewer')
        self.active   = doc.get('active', True)
    def get_id(self):
        return self.id

@login_manager.user_loader
def load_user(user_id):
    from bson import ObjectId
    from pymongo import MongoClient
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        doc = client['weather_rss']['users'].find_one({'_id': ObjectId(user_id)})
        client.close()
        return User(doc) if doc else None
    except:
        return None

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fpren-secret-2026-change-me")
login_manager.init_app(app)
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
status_col       = db[COLLECTION]
alerts_col        = db["nws_alerts"]
zone_wavs_col     = db["zone_alert_wavs"]
airport_metar_col = db["airport_metar"]
fl_traffic_col    = db["fl_traffic"]
school_col        = db["school_closings"]

# -------------------- TEMPLATE ------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link rel="icon" type="image/png" href="/static/fpren.png">
<title>FPREN | Florida Public Radio Emergency Network</title>
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
  .quick-q { font-size:11px;padding:3px 8px;border:1px solid #ccc;border-radius:12px;background:#f5f5f5;cursor:pointer;color:#333; }
  .quick-q:hover { background:#003087;color:#fff;border-color:#003087; }
  .chat-msg { animation:fadeIn .2s ease; }
  .user-msg { background:#003087;color:#fff;border-radius:6px;padding:10px 12px;font-size:13px;max-width:80%;align-self:flex-end; }
  .agent-msg { background:#e8f0fb;color:#222;border-radius:6px;padding:10px 12px;font-size:13px;max-width:88%;align-self:flex-start; }
  .agent-thinking { background:#f0f0f0;color:#888;font-style:italic;border-radius:6px;padding:8px 12px;font-size:12px;align-self:flex-start; }
  @keyframes fadeIn { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:none} }

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

  /* ---- Stream Control card ---- */
  .sc-dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:middle; }
  .sc-dot.live { background:#2e7d32; }
  .sc-dot.offline { background:#c62828; }
  .btn-sc-stop  { padding:5px 12px; background:#c62828; color:#fff; border:none;
    border-radius:4px; font-size:0.82rem; font-weight:700; cursor:pointer; }
  .btn-sc-stop:hover  { background:#b71c1c; }
  .btn-sc-start { padding:5px 12px; background:#2e7d32; color:#fff; border:none;
    border-radius:4px; font-size:0.82rem; font-weight:700; cursor:pointer; }
  .btn-sc-start:hover { background:#1b5e20; }
  .btn-sc-restart { padding:7px 16px; background:#0077aa; color:#fff; border:none;
    border-radius:4px; font-size:0.875rem; font-weight:700; cursor:pointer; }
  .btn-sc-restart:hover { background:#005f8a; }
  .sc-status-row { display:flex; align-items:center; gap:10px; margin-top:12px; flex-wrap:wrap; }
  .sc-engine-msg { font-size:0.8rem; color:#666; font-style:italic; }

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

  /* ---- Icecast tab ---- */
  .ice-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:18px; max-width:1100px; padding:4px 0; }
  .ice-card { background:#fff; border:1px solid #ddd; border-radius:8px; overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,.07); }
  .ice-card-hdr { padding:10px 16px; font-size:1rem; font-weight:700; display:flex; justify-content:space-between; align-items:center; }
  .ice-hdr-live { background:#1b5e20; color:#fff; }
  .ice-hdr-off  { background:#555; color:#ccc; }
  .ice-badge { font-size:0.72rem; font-weight:700; padding:2px 8px; border-radius:10px; }
  .ice-badge-live { background:#a5d6a7; color:#1b5e20; }
  .ice-badge-off  { background:#888; color:#fff; }
  .ice-body { padding:12px 16px; font-size:0.85rem; line-height:2; color:#444; }
  .ice-body strong { color:#0077aa; }
  .ice-stat { display:flex; justify-content:space-between; border-bottom:1px solid #f0f0f0; padding:3px 0; }
  .ice-stat:last-child { border-bottom:none; }

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

  /* ---- Playlist tab ---- */
  .pl-now-playing {
    background:#111; color:#fff; border-radius:8px; padding:16px 22px;
    margin-bottom:20px; max-width:700px; display:flex; align-items:center; gap:18px;
  }
  .pl-now-icon { font-size:2rem; }
  .pl-now-title { font-size:1.1rem; font-weight:700; color:#00cfff; }
  .pl-now-meta  { font-size:0.82rem; color:#aaa; margin-top:4px; }
  .pl-card { background:#fff; border:1px solid #ddd; border-radius:8px;
             max-width:700px; margin-bottom:20px; overflow:hidden;
             box-shadow:0 1px 4px rgba(0,0,0,.07); }
  .pl-card-hdr { background:#0077aa; color:#fff; padding:10px 18px;
                 display:flex; justify-content:space-between; align-items:center; }
  .pl-card-hdr-title { font-size:1rem; font-weight:700; }
  .pl-card-hdr-sub   { font-size:0.78rem; opacity:.8; }
  .pl-slots { width:100%; border-collapse:collapse; font-size:0.88rem; }
  .pl-slots th { background:#e8f4fb; color:#0077aa; padding:7px 14px;
                 text-align:left; font-weight:700; font-size:0.8rem;
                 text-transform:uppercase; letter-spacing:.04em; }
  .pl-slots td { padding:8px 14px; border-bottom:1px solid #f0f0f0; }
  .pl-slots tr:last-child td { border-bottom:none; }
  .pl-slots tr:hover td { background:#f5faff; }
  .pl-cat { display:inline-block; background:#e0f4ff; color:#0077aa;
            border-radius:3px; padding:1px 7px; font-size:0.78rem;
            font-family:monospace; }
  .pl-switcher { display:flex; gap:10px; align-items:center; margin-bottom:20px; flex-wrap:wrap; }
  .pl-select { border:1px solid #bbb; border-radius:4px; padding:6px 10px;
               font-size:0.875rem; background:#fff; cursor:pointer; min-width:200px; }
  .pl-btn-assign { padding:6px 16px; background:#0077aa; color:#fff; border:none;
                   border-radius:4px; font-size:0.875rem; font-weight:700; cursor:pointer; }
  .pl-btn-assign:hover { background:#005f8a; }
  .pl-status { font-size:0.8rem; font-style:italic; color:#666; }
</style>
</head>
<body>

<!-- Header with logo -->
<header class="site-header">
  <img src="/static/fpren.png" alt="FPREN Logo">
  <div>
    <div class="site-header-title">FPREN — Florida Public Radio Emergency Network</div>
    <div class="site-header-sub">Weather &bull; Traffic &bull; Alerts &bull; Icecast</div>
  </div>
</header>

<!-- Tab navigation -->
<nav class="tab-nav">
  <button class="active" onclick="showTab('weather',this)">Weather</button>
  <button onclick="showTab('playlist',this)">Playlist</button>
  <button onclick="showTab('icecast',this)">Icecast</button>
  <button onclick="showTab('data',this)">Alerts &amp; Data</button>
  <button onclick="showTab('airports',this)">&#9992; Airports</button>
  <button onclick="showTab('reports',this)">&#128196; Reports</button>
  <button onclick="showTab('zones',this)">&#128205; Zones</button>
  <button onclick="showTab('upload',this)">&#8679; Upload</button>
  <button onclick="showTab('agent',this)">&#x1F916; AI Agent</button>
  <button onclick="showTab('config',this)">&#9881; Config</button>
</nav>

<!-- ===== CONFIG TAB ===== -->
<div id="tab-config" class="tab-panel">
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

  <!-- Stream Control card -->
  <div class="cfg-card">
    <h2>Stream Control</h2>
    <table class="cfg-table">
      <thead>
        <tr><th>Stream</th><th>Port</th><th>Mount</th><th>Status</th><th>Action</th></tr>
      </thead>
      <tbody id="sc-rows">
        <tr><td colspan="5" style="color:#aaa;text-align:center;padding:18px;">Loading&hellip;</td></tr>
      </tbody>
    </table>
    <div class="sc-status-row">
      <button class="btn-sc-restart" onclick="scRestartEngine()">&#x21BA; Restart Broadcast Engine</button>
      <span id="sc-engine-msg" class="sc-engine-msg"></span>
    </div>
    <p class="cfg-note">Stop kills the audio source for that mount point. Restart Engine reconnects all stream sources via the broadcast engine service.</p>
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
  <div class="cfg-card">
    <h2>User Management</h2>
    <table class="cfg-table"><thead><tr><th>Username</th><th>Role</th><th>Actions</th></tr></thead>
    <tbody id="users-tbody"><tr><td colspan="3" style="color:#aaa;text-align:center;padding:18px;">Loading...</td></tr></tbody></table>
    <div style="margin-top:16px;border-top:1px solid #e9ecef;padding-top:16px;">
      <h3 style="margin-bottom:12px;font-size:1rem;">Add New User</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:8px;">
        <label style="font-size:0.85rem;">Username<br><input id="new-username" class="smtp-input" type="text" placeholder="username"></label>
        <label style="font-size:0.85rem;">Password<br><input id="new-password" class="smtp-input" type="password" placeholder="password"></label>
        <label style="font-size:0.85rem;">Role<br><select id="new-role" class="smtp-input"><option value="admin">Admin</option><option value="operator">Operator</option><option value="viewer" selected>Viewer</option></select></label>
        <div style="display:flex;align-items:flex-end;"><button class="btn-smtp-save" onclick="addUser()">Add User</button></div>
      </div>
      <span id="user-status" style="font-size:0.85rem;margin-top:8px;display:block;"></span>
    </div>
    <p class="cfg-note">Admins have full access. Operators can upload content. Viewers are read-only.</p>
  </div>
</div>
<!-- ===== PLAYLIST TAB ===== -->
<div id="tab-playlist" class="tab-panel">
  <div id="pl-load" style="text-align:center;padding:40px;color:#888;font-size:1rem;">Click the Playlist tab to load&hellip;</div>
  <div id="pl-content" style="display:none;">
    <div id="pl-now"></div>
    <div class="pl-switcher">
      <strong style="font-size:0.9rem;">Stream 8000 Playlist:</strong>
      <select id="pl-select" class="pl-select"></select>
      <button class="pl-btn-assign" onclick="assignPlaylist()">Assign</button>
      <span id="pl-status" class="pl-status"></span>
    </div>
    <div id="pl-slots-wrap"></div>
  </div>
</div>

<!-- ===== ICECAST TAB ===== -->
<div id="tab-icecast" class="tab-panel">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap;">
    <button class="btn-smtp-test" onclick="testStreamAlert()">&#128276; Test Port 8000 Alert Email</button>
    <button class="btn-smtp-save" onclick="refreshIcecast()">&#x21BA; Refresh</button>
    <span id="ice-alert-status" class="smtp-status"></span>
  </div>
  <div id="ice-load" style="text-align:center;padding:40px;color:#888;font-size:1rem;">Click the Icecast tab to load stream status&hellip;</div>
  <div id="ice-grid" class="ice-grid"></div>
</div>

<!-- ===== WEATHER TAB ===== -->
<div id="tab-weather" class="tab-panel active">
  <div id="wx-load" style="text-align:center;padding:40px;color:#888;font-size:1rem;">Click the Weather tab to load forecast data&hellip;</div>
  <div id="wx-grid" class="wx-grid"></div>
</div>

<!-- ===== DATA TAB ===== -->
<div id="tab-data" class="tab-panel">
  <div style="display:flex; justify-content:space-between; align-items:center; padding:0 0 4px; flex-wrap:wrap; gap:8px;">
    <small id="data-refreshed" style="color:#888;">Loading...</small>
    <button class="feedback-btn" onclick="document.getElementById('feedbackDialog').showModal()">Feedback</button>
  </div>

  <dialog id="feedbackDialog">
    <h3 style="margin-top:0">Send Feedback</h3>
    <form method="post" action="/feedback">
      <label>Name (optional)<input type="text" name="name" placeholder="Your name"></label>
      <label>Message <span style="color:red">*</span><textarea name="message" placeholder="Share your feedback..." required></textarea></label>
      <div class="dialog-actions">
        <button type="button" class="btn-secondary" onclick="document.getElementById('feedbackDialog').close()">Cancel</button>
        <button type="submit" class="btn-primary">Submit</button>
      </div>
    </form>
  </dialog>

  <div id="data-content">
    <div style="text-align:center;padding:40px;color:#888;">Loading data...</div>
  </div>

  <div style="margin-top:24px;">
    <!-- Transcode panel -->
    <div style="background:#fff;border:1px solid #ddd;border-radius:6px;padding:14px 18px;margin-bottom:18px;max-width:860px;">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <strong style="font-size:0.95rem;color:#0077aa;">&#127908; Alert Transcoding</strong>
        <span id="tc-status" style="font-size:0.82rem;color:#666;"></span>
        <button class="btn-smtp-save" onclick="tcRunNow()" id="tc-btn" style="margin-left:auto;">&#9654; Transcode Now</button>
        <button class="btn-smtp-test" onclick="tcRefreshStatus()" style="padding:7px 12px;">&#x21BA;</button>
      </div>
      <div id="tc-counts" style="margin-top:8px;font-size:0.82rem;color:#555;"></div>
      <div id="tc-progress" style="margin-top:10px;display:none;"></div>
    </div>

    <!-- Audio library -->
    <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:8px;">
      <h2 style="margin:0;">Alert Audio Library</h2>
      <span id="za-total" style="font-size:0.82rem;color:#666;"></span>
      <label style="font-size:0.82rem;font-weight:600;display:flex;align-items:center;gap:6px;">
        Zone:
        <select id="za-zone-sel" class="zone-select" style="min-width:150px;"
                onchange="loadZoneAudio(1, this.value)">
          <option value="all_florida">all florida</option>
        </select>
      </label>
    </div>
    <div id="za-container"><div style="color:#888;padding:20px 0;">Loading audio library&hellip;</div></div>
  </div>
</div><!-- end tab-data -->

<div id="tab-airports" class="tab-panel">
  <div style="display:flex; justify-content:space-between; align-items:center; padding:0 0 8px; flex-wrap:wrap; gap:8px;">
    <small id="ap-refreshed" style="color:#888;">Loading...</small>
    <button class="btn-smtp-test" onclick="_tabLoaded['airports']=0; loadAirports();" style="padding:7px 12px;">&#x21BA; Refresh</button>
  </div>

  <!-- TSA Wait Times -->
  <h2>TSA Security Wait Times</h2>
  <p style="color:#888;font-size:0.85rem;margin:-8px 0 12px;">Live data available for MCO and MIA. Other Florida airports do not publish public wait-time APIs.</p>
  <div id="ap-tsa-content">
    <div style="text-align:center;padding:30px;color:#888;">Loading TSA wait times&hellip;</div>
  </div>

  <!-- Airport Weather (METAR) -->
  <h2 style="margin-top:28px;">Airport Weather <small id="ap-metar-count"></small></h2>
  <div id="ap-metar-content">
    <div style="text-align:center;padding:30px;color:#888;">Loading METAR data&hellip;</div>
  </div>
</div><!-- end tab-airports -->

<div id="tab-agent" class="tab-panel">
  <div style="max-width:860px;margin:0 auto;padding:8px 0;">

    <!-- Situation Report card -->
    <div style="background:#fff;border:1px solid #ddd;border-radius:6px;padding:16px;margin-bottom:18px;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
        <span style="font-size:18px;">&#x1F4CB;</span>
        <strong style="font-size:15px;">Live Situation Report</strong>
        <button onclick="loadSituationReport()" style="margin-left:auto;padding:4px 12px;border:1px solid #003087;border-radius:4px;background:#003087;color:#fff;cursor:pointer;font-size:12px;">&#x21BA; Refresh</button>
        {% if current_user.role == 'admin' %}
        <button onclick="generateSituationReport()" id="btn-gen-sit" style="padding:4px 12px;border:1px solid #e07020;border-radius:4px;background:#e07020;color:#fff;cursor:pointer;font-size:12px;">&#x2728; Generate New</button>
        {% endif %}
      </div>
      <div id="sit-report-text" style="font-size:13px;line-height:1.65;color:#333;min-height:60px;">
        <span style="color:#aaa;">Loading&hellip;</span>
      </div>
      <div id="sit-report-meta" style="font-size:11px;color:#999;margin-top:8px;"></div>
    </div>

    <!-- Chat interface -->
    <div style="background:#fff;border:1px solid #ddd;border-radius:6px;padding:16px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
        <span style="font-size:18px;">&#x1F916;</span>
        <strong style="font-size:15px;">FPREN AI Operator Assistant</strong>
        <span style="font-size:11px;color:#888;margin-left:auto;">Powered by UF LiteLLM &bull; reads live MongoDB data</span>
      </div>

      <div id="agent-chat-history" style="height:340px;overflow-y:auto;border:1px solid #eee;border-radius:4px;padding:12px;background:#fafafa;margin-bottom:10px;display:flex;flex-direction:column;gap:10px;">
        <div class="chat-msg agent-msg" style="background:#e8f0fb;border-radius:6px;padding:10px 12px;font-size:13px;max-width:85%;align-self:flex-start;">
          Hello! I&#39;m the FPREN AI assistant. I have access to live alerts, weather observations, FL511 traffic, Waze incidents, Census data, evacuation zones, and stream status. Ask me anything about current conditions in Florida.
        </div>
      </div>

      <div style="display:flex;gap:8px;">
        <input id="agent-input" type="text" placeholder="e.g. What&#39;s the worst traffic in Tampa right now?"
          style="flex:1;padding:8px 12px;border:1px solid #ccc;border-radius:4px;font-size:13px;"
          onkeydown="if(event.key==='Enter') sendAgentMessage()">
        <button onclick="sendAgentMessage()" id="btn-agent-send"
          style="padding:8px 18px;background:#003087;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px;white-space:nowrap;">
          Send &#x27A4;
        </button>
      </div>
      <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;">
        <span style="font-size:11px;color:#888;">Quick questions:</span>
        <button onclick="quickAsk('What active NWS alerts are there in Florida right now?')" class="quick-q">Active alerts</button>
        <button onclick="quickAsk('Summarise current traffic conditions across Florida')" class="quick-q">Traffic summary</button>
        <button onclick="quickAsk('What is the current weather at KGNV, KTPA, and KMIA?')" class="quick-q">Weather obs</button>
        <button onclick="quickAsk('Which Florida county has the highest vulnerability score?')" class="quick-q">Vulnerability</button>
        <button onclick="quickAsk('Are all 9 FPREN Icecast zone streams currently live?')" class="quick-q">Stream status</button>
        <button onclick="quickAsk('What are the hurricane evacuation zones for Alachua county?')" class="quick-q">Evac zones</button>
      </div>
    </div>

  </div>
</div><!-- end tab-agent -->

<div id="toast"></div>

<script>
const ZONES = {{ zones | tojson }};

const _TAB_TTL = { weather: 600, playlist: 60, icecast: 30, data: 60, airports: 120 };
const _tabLoaded = {};
function _isStale(tab) {
  const ttl = _TAB_TTL[tab] || 60;
  return !_tabLoaded[tab] || (Date.now() - _tabLoaded[tab]) > ttl * 1000;
}
function _markLoaded(tab) { _tabLoaded[tab] = Date.now(); }

// Wrap every fetch() with a 10-second timeout so a slow endpoint cannot freeze the page
(function() {
  const _orig = window.fetch;
  window.fetch = function(url, opts) {
    const ctrl = new AbortController();
    const id = setTimeout(() => ctrl.abort(), 10000);
    return _orig(url, Object.assign({}, opts, { signal: ctrl.signal }))
      .finally(() => clearTimeout(id));
  };
})();

// Guard: set to true while responding to a remote sync so we don't echo back
let _syncSwitching = false;

function showTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  localStorage.setItem('activeTab', name);
  // Push tab change to shared state so desktop app can sync (skip echo-back)
  if (!_syncSwitching) {
    fetch('/api/state', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({active_tab: name})
    }).catch(() => {});
  }
  if (name === 'weather')  loadWeather();
  if (name === 'playlist') loadPlaylist();
  if (name === 'icecast')  loadIcecast();
  if (name === 'data')     loadDataTab();
  if (name === 'airports') loadAirports();
  if (name === 'reports')  loadReports();
  if (name === 'config')   { loadConfig(); loadStreamControl(); loadSmtp(); loadUsers(); }
  if (name === 'zones')    loadZones();
  if (name === 'upload')   { initUpload(); loadUploadList(); }
  if (name === 'agent')    loadSituationReport();
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

// Restore last active tab and start auto-refresh
(function() {
  const saved = localStorage.getItem('activeTab') || 'weather';
  // Never auto-restore config tab on load (its loaders are deferred to user click).
  // Fall back to 'weather' if the saved tab panel doesn't exist in the DOM.
  const panel = document.getElementById('tab-' + saved);
  const effectiveTab = (saved === 'config' || !panel) ? 'weather' : saved;

  for (const btn of document.querySelectorAll('.tab-nav button')) {
    if ((btn.getAttribute('onclick') || '').includes("'" + effectiveTab + "'")) {
      showTab(effectiveTab, btn);
      break;
    }
  }

  // These two always run on page load regardless of which tab is active
  loadWeather();
  initUpload();

  // Auto-refresh intervals (in-place, no page reload)
  setInterval(loadDataTab,   60000);   // data tab: every 60s
  setInterval(loadAirports, 120000);   // airports: every 2 min
  setInterval(loadIcecast,   30000);   // icecast: every 30s
  setInterval(loadPlaylist,  60000);   // playlist: every 60s
  setInterval(loadWeather,  600000);   // weather: every 10 min

  // ── Bidirectional sync with desktop app ─────────────────────────────
  // Poll /api/sync every 5 s; only pull full state when the token changes.
  let _syncToken = null;
  function _pollSync() {
    fetch('/api/sync')
      .then(r => r.json())
      .then(d => {
        if (!d || d._error) return;
        const tok = d.token;
        if (_syncToken !== null && tok !== _syncToken) {
          // State changed externally — switch to the new active tab if different
          const remoteTab = d.active_tab;
          if (remoteTab && remoteTab !== localStorage.getItem('activeTab')) {
            const panel = document.getElementById('tab-' + remoteTab);
            if (panel) {
              for (const btn of document.querySelectorAll('.tab-nav button')) {
                if ((btn.getAttribute('onclick') || '').includes("'" + remoteTab + "'")) {
                  _syncSwitching = true;
                  showTab(remoteTab, btn);
                  _syncSwitching = false;
                  break;
                }
              }
            }
          }
        }
        _syncToken = tok;
      })
      .catch(() => {});
  }
  setInterval(_pollSync, 5000);
  _pollSync();
})();

// ---- Stream Control ----
function loadStreamControl() {
  fetch('/api/icecast')
    .then(r => r.json())
    .then(streams => {
      document.getElementById('sc-rows').innerHTML = streams.map(s => {
        const dot = s.live
          ? '<span class="sc-dot live"></span>Live'
          : '<span class="sc-dot offline"></span>Offline';
        const btn = s.live
          ? `<button class="btn-sc-stop"  onclick="scStop('${s.id}','${s.mount}')">Stop</button>`
          : `<button class="btn-sc-start" onclick="scStart()">Start</button>`;
        return `<tr>
          <td><strong>${s.label}</strong></td>
          <td><span class="port-tag">:${s.port}</span></td>
          <td><code>${s.mount}</code></td>
          <td>${dot}</td>
          <td>${btn}</td>
        </tr>`;
      }).join('');
    })
    .catch(() => {
      document.getElementById('sc-rows').innerHTML =
        '<tr><td colspan="5" style="color:#c62828;text-align:center;">Failed to load stream status</td></tr>';
    });
}

function scStop(streamId, mount) {
  if (!confirm('Stop audio source for ' + mount + '?')) return;
  fetch('/api/streams/' + streamId + '/stop', {method: 'POST'})
    .then(r => r.json())
    .then(d => { toast(d.message || (d.ok ? 'Stopped' : 'Error'), d.ok); loadStreamControl(); })
    .catch(() => toast('Request failed', false));
}

function scStart() {
  fetch('/api/streams/start-engine', {method: 'POST'})
    .then(r => r.json())
    .then(d => { toast(d.message || (d.ok ? 'Starting\u2026' : 'Error'), d.ok); setTimeout(loadStreamControl, 4000); })
    .catch(() => toast('Request failed', false));
}

function scRestartEngine() {
  if (!confirm('Restart the broadcast engine? All streams will reconnect in a few seconds.')) return;
  const msg = document.getElementById('sc-engine-msg');
  msg.textContent = 'Restarting\u2026';
  fetch('/api/streams/restart-engine', {method: 'POST'})
    .then(r => r.json())
    .then(d => {
      toast(d.message || (d.ok ? 'Engine restarting' : 'Error'), d.ok);
      msg.textContent = d.message || '';
      setTimeout(() => { msg.textContent = ''; loadStreamControl(); }, 5000);
    })
    .catch(() => { toast('Request failed', false); msg.textContent = ''; });
}

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

// ---- Playlist ----
let _plData   = null;

function loadPlaylist() {
  if (!_isStale('playlist')) return;
  _markLoaded('playlist');
  document.getElementById('pl-load').textContent = 'Loading playlist\u2026';
  fetch('/api/playlist')
    .then(r => r.json())
    .then(data => {
      _plData = data;
      document.getElementById('pl-load').style.display = 'none';
      document.getElementById('pl-content').style.display = 'block';

      // Now Playing
      const np = data.now_playing;
      document.getElementById('pl-now').innerHTML = np
        ? `<div class="pl-now-playing">
             <div class="pl-now-icon">&#9654;</div>
             <div>
               <div class="pl-now-title">${np.title}</div>
               <div class="pl-now-meta">Category: ${np.category} &mdash; Started: ${np.started_at}</div>
             </div>
           </div>`
        : `<div class="pl-now-playing"><div class="pl-now-icon">&#9646;&#9646;</div><div class="pl-now-meta">Nothing currently playing</div></div>`;

      // Playlist switcher
      const sel = document.getElementById('pl-select');
      sel.innerHTML = data.available.map(p =>
        `<option value="${p.file}" ${p.file === data.active ? 'selected' : ''}>${p.name} (${p.file})</option>`
      ).join('');

      renderSlots(data.active, data.available);
    })
    .catch(() => {
      document.getElementById('pl-load').textContent = 'Could not load playlist data.';
      _tabLoaded['playlist'] = 0;
    });
}

function renderSlots(activeFile, available) {
  const pl = available.find(p => p.file === activeFile);
  if (!pl) return;
  const rows = pl.slots.map((s, i) =>
    `<tr>
      <td class="center" style="color:#888;font-size:0.8rem;">${i + 1}</td>
      <td>${s.label}</td>
      <td><span class="pl-cat">${s.category}</span></td>
      <td class="center">${s.top_of_hour ? '&#9679;' : ''}</td>
    </tr>`
  ).join('');
  document.getElementById('pl-slots-wrap').innerHTML = `
    <div class="pl-card">
      <div class="pl-card-hdr">
        <span class="pl-card-hdr-title">${pl.name}</span>
        <span class="pl-card-hdr-sub">${pl.description}</span>
      </div>
      <table class="pl-slots">
        <thead><tr><th>#</th><th>Label</th><th>Category</th><th>Top of Hour</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function assignPlaylist() {
  const file = document.getElementById('pl-select').value;
  const st = document.getElementById('pl-status');
  st.textContent = 'Saving\u2026';
  fetch('/api/playlist/assign', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({stream_id: 'stream_8000', file})
  })
  .then(r => r.json())
  .then(d => {
    st.textContent = d.message;
    st.style.color = d.ok ? '#2e7d32' : '#c0392b';
    if (d.ok && _plData) renderSlots(file, _plData.available);
  })
  .catch(() => { st.textContent = 'Request failed'; st.style.color = '#c0392b'; });
}

// ---- Stream alert test ----
function testStreamAlert() {
  const el = document.getElementById('ice-alert-status');
  el.textContent = 'Sending test alert…';
  el.style.color = '#666';
  fetch('/api/stream-alert/test', {method:'POST'})
    .then(r => r.json())
    .then(d => {
      el.textContent = d.message;
      el.style.color = d.ok ? '#2e7d32' : '#c0392b';
    })
    .catch(() => { el.textContent = 'Request failed'; el.style.color = '#c0392b'; });
}

// ---- Icecast ----
function refreshIcecast() {
  _tabLoaded['icecast'] = 0;
  loadIcecast();
}

function loadIcecast() {
  if (!_isStale('icecast')) return;
  _markLoaded('icecast');
  document.getElementById('ice-load').textContent = 'Loading stream status\u2026';
  fetch('/api/icecast')
    .then(r => r.json())
    .then(streams => {
      const grid = document.getElementById('ice-grid');
      document.getElementById('ice-load').style.display = 'none';
      grid.innerHTML = streams.map(s => {
        const live = s.live;
        const hdrCls  = live ? 'ice-hdr-live' : 'ice-hdr-off';
        const badgeCls = live ? 'ice-badge-live' : 'ice-badge-off';
        const badgeTxt = live ? 'LIVE' : 'OFFLINE';
        const rows = [
          ['Mount',     s.mount],
          ['Port',      s.port],
          ['Listeners', live ? s.listeners : '—'],
          ['Bitrate',   live && s.bitrate ? s.bitrate + ' kbps' : '—'],
          ['Format',    live && s.format  ? s.format  : '—'],
          ['Title',     live && s.title   ? s.title   : '—'],
        ].map(([k,v]) => `<div class="ice-stat"><span>${k}</span><strong>${v}</strong></div>`).join('');
        return `<div class="ice-card">
          <div class="ice-card-hdr ${hdrCls}">
            ${s.label}
            <span class="ice-badge ${badgeCls}">${badgeTxt}</span>
          </div>
          <div class="ice-body">${rows}</div>
        </div>`;
      }).join('');
    })
    .catch(() => {
      document.getElementById('ice-load').textContent = 'Could not load Icecast data.';
      _tabLoaded['icecast'] = 0;
    });
}

// ---- Weather ----

function loadZones() {
  fetch('/api/zones')
    .then(r => r.json())
    .then(d => {
      const tbody = document.getElementById('zones-tbody');
      tbody.innerHTML = d.zones.map(z => {
        const type = z.catch_all ? 'Catch-All' : 'County';
        const counties = z.catch_all ? 'All Florida' : (z.counties||[]).slice(0,4).join(', ') + (z.counties.length>4?'...':'');
        const age = z.cleanup ? z.cleanup.max_age_hours+'h' : '--';
        const files = z.cleanup && z.cleanup.max_files ? z.cleanup.max_files : '--';
        return '<tr><td>'+z.zone_id+'</td><td>'+type+'</td><td>'+counties+'</td><td>'+age+'</td><td>'+files+'</td></tr>';
      }).join('');
    }).catch(() => toast('Zones fetch failed', false));
}

function initUpload() {
  var drop = document.getElementById('upload-drop');
  var input = document.getElementById('upload-input');
  if (!drop || !input) return;
  drop.onclick = function() { input.click(); };
  drop.ondragover = function(e) { e.preventDefault(); drop.style.borderColor='#0077aa'; };
  drop.ondragleave = function() { drop.style.borderColor='#ccc'; };
  drop.ondrop = function(e) { e.preventDefault(); drop.style.borderColor='#ccc'; uploadFiles(e.dataTransfer.files); };
  input.onchange = function() { uploadFiles(input.files); };
}
function uploadFiles(files) {
  var folder = document.getElementById('upload-folder').value;
  var status = document.getElementById('upload-status');
  status.innerHTML = '';
  Array.from(files).forEach(function(file) {
    var fd = new FormData();
    fd.append('folder', folder);
    fd.append('file', file);
    fetch('/api/upload', {method:'POST', body:fd})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var p = document.createElement('p');
        p.style.color = d.ok ? 'green' : 'red';
        p.textContent = d.message;
        status.appendChild(p);
        if (d.ok) loadUploadList();
      });
  });
}
function loadUploadList() {
  var folder = document.getElementById('upload-folder').value;
  fetch('/api/upload/list')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var files = d[folder] || [];
      var el = document.getElementById('upload-file-list');
      if (files.length === 0) { el.innerHTML='<p style="color:#aaa;">No files yet</p>'; return; }
      var rows = files.map(function(f) {
        return '<tr><td style="padding:6px;">'+f.name+'</td><td style="text-align:right;padding:6px;">'+f.size_kb+' KB</td><td style="text-align:center;padding:6px;"><button onclick="deleteUpload(\''+folder+'\',\''+f.name+'\')" style="color:red;border:none;background:none;cursor:pointer;">&#128465;</button></td></tr>';
      }).join('');
      el.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:0.9rem;"><thead><tr><th style="text-align:left;padding:6px;border-bottom:1px solid #eee;">File</th><th style="text-align:right;padding:6px;border-bottom:1px solid #eee;">Size</th><th style="padding:6px;border-bottom:1px solid #eee;">Action</th></tr></thead><tbody>'+rows+'</tbody></table>';
    });
}
function deleteUpload(folder, filename) {
  if (confirm('Delete ' + filename + '?')) {
    fetch('/api/upload/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({folder:folder,filename:filename})})
      .then(function(r) { return r.json(); })
      .then(function(d) { toast(d.message, d.ok); if(d.ok) loadUploadList(); });
  }
}


function loadUsers() {
  fetch('/api/users')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var tbody = document.getElementById('users-tbody');
      if (!d.users || !d.users.length) {
        tbody.innerHTML = '<tr><td colspan="3" style="color:#aaa;text-align:center;padding:18px;">No users found</td></tr>';
        return;
      }
      tbody.innerHTML = d.users.map(function(u) {
        var isMe = u.username === '' + (typeof currentUsername !== 'undefined' ? currentUsername : '') + '';
        return '<tr>' +
          '<td style="padding:8px;">' + u.username + (isMe ? ' <span style="color:#0077aa;font-size:0.8rem;">(you)</span>' : '') + '</td>' +
          '<td style="padding:8px;">' + u.role + '</td>' +
          '<td style="padding:8px;">' +
            '<button onclick="resetPassword(\'' + u.username + '\')" style="font-size:0.8rem;padding:3px 8px;margin-right:4px;border:1px solid #ccc;border-radius:3px;cursor:pointer;">Reset PW</button>' +
            (!isMe ? '<button onclick="deleteUser(\'' + u.username + '\')" style="font-size:0.8rem;padding:3px 8px;border:1px solid #fcc;border-radius:3px;cursor:pointer;color:red;">Delete</button>' : '') +
          '</td></tr>';
      }).join('');
    })
    .catch(function() { toast('Failed to load users', false); });
}
function addUser() {
  var username = document.getElementById('new-username').value.trim();
  var password = document.getElementById('new-password').value.trim();
  var role = document.getElementById('new-role').value;
  var status = document.getElementById('user-status');
  if (!username || !password) { status.style.color='red'; status.textContent='Username and password required'; return; }
  fetch('/api/users/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:username, password:password, role:role})})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      status.style.color = d.ok ? 'green' : 'red';
      status.textContent = d.message;
      if (d.ok) {
        document.getElementById('new-username').value = '';
        document.getElementById('new-password').value = '';
        loadUsers();
      }
    });
}
function deleteUser(username) {
  if (!confirm('Delete user ' + username + '?')) return;
  fetch('/api/users/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:username})})
    .then(function(r) { return r.json(); })
    .then(function(d) { toast(d.message, d.ok); if (d.ok) loadUsers(); });
}
function resetPassword(username) {
  var pw = prompt('New password for ' + username + ':');
  if (!pw) return;
  fetch('/api/users/password', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:username, password:pw})})
    .then(function(r) { return r.json(); })
    .then(function(d) { toast(d.message, d.ok); });
}

function loadWeather() {
  if (!_isStale('weather')) return;
  _markLoaded('weather');
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
      _tabLoaded['weather'] = 0;
    });
}

// ---- Data Tab ----
function loadDataTab() {
  if (!_isStale('data')) return;
  _markLoaded('data');
  fetch('/api/data-tab')
    .then(r => r.json())
    .then(d => {
      document.getElementById('data-refreshed').textContent = 'Updated ' + d.now;

      let html = '';

      // Now playing
      if (d.now_playing) {
        html += `<p style="margin:8px 0;font-size:0.95rem;">
          <strong>&#9654; Now Playing:</strong> ${d.now_playing.title}
          <span style="color:#666;">[${d.now_playing.category}]</span>
          &mdash; <small>started ${d.now_playing.started_at}</small></p>`;
      }

      // NWS Alerts
      html += `<h2>NWS Alerts <small>(${d.alerts.length} most recent)</small></h2>
        <table><tr><th>Event</th><th>Headline</th><th>Severity</th><th>Areas</th><th>Sender</th><th>Sent</th><th>Audio</th></tr>`;
      if (d.alerts.length) {
        d.alerts.forEach(a => {
          const audio = a.audio_id
            ? `<a href="/audio/download/${a.audio_id}" download class="badge badge-yes" style="text-decoration:none;">&#8681; ${a.audio_ext||'MP3'}</a>`
            : `<span class="badge badge-no">Pending</span>`;
          html += `<tr class="${a.sev_class}"><td><strong>${a.event}</strong></td><td>${a.headline}</td>
            <td class="center">${a.severity}</td><td>${a.area_desc}</td><td>${a.sender}</td>
            <td class="center">${a.sent}</td><td class="center">${audio}</td></tr>`;
        });
      } else {
        html += `<tr><td colspan="7" class="no-data">No alerts in database</td></tr>`;
      }
      html += '</table>';

      // FL Traffic
      html += `<h2>FL Traffic <small>(${d.traffic.length} active incidents)</small></h2>
        <table><tr><th>Type</th><th>Road</th><th>Location</th><th>County</th><th>Severity</th><th>Last Updated</th></tr>`;
      if (d.traffic.length) {
        d.traffic.forEach(t => {
          html += `<tr><td>${t.type}</td><td>${t.road}</td><td>${t.location}</td>
            <td>${t.county}</td><td class="center">${t.severity}</td><td class="center">${t.last_updated}</td></tr>`;
        });
      } else {
        html += `<tr><td colspan="6" class="no-data">No traffic incidents</td></tr>`;
      }
      html += '</table>';

      // School closings
      html += `<h2>School Closings &amp; Delays <small>(Alachua County)</small></h2>
        <table><tr><th>Title</th><th>Type</th><th>Published</th><th>Fetched</th></tr>`;
      if (d.school.length) {
        d.school.forEach(s => {
          html += `<tr class="sev-moderate"><td>${s.title}</td><td class="center">${s.closure_type}</td>
            <td class="center">${s.published_date}</td><td class="center">${s.fetched_at}</td></tr>`;
        });
      } else {
        html += `<tr><td colspan="4" class="no-data">No active school closings or delays</td></tr>`;
      }
      html += '</table>';

      // RSS Feed Status
      html += `<h2>RSS Feed Status</h2>
        <table><tr><th>Feed Filename</th><th>Last Success</th><th>Age (min)</th><th>File Size (KB)</th><th>Status</th></tr>`;
      if (d.feeds.length) {
        d.feeds.forEach(f => {
          html += `<tr class="${f.row_class}"><td>${f.filename}</td><td class="center">${f.last_success||'—'}</td>
            <td class="center">${f.age_min||'—'}</td><td class="center">${f.file_size_kb||'—'}</td>
            <td class="center">${f.status}</td></tr>`;
        });
      } else {
        html += `<tr><td colspan="5" class="no-data">No feed status data</td></tr>`;
      }
      html += '</table>';

      document.getElementById('data-content').innerHTML = html;
      tcRefreshStatus();
      loadZoneAudio(1, document.getElementById('za-zone-sel') ? document.getElementById('za-zone-sel').value : 'all_florida');
    })
    .catch(() => {
      document.getElementById('data-refreshed').textContent = 'Failed to load — retrying...';
      _tabLoaded['data'] = 0;  // allow immediate retry
    });
}

// ---- Airports Tab ----
function loadAirports() {
  if (!_isStale('airports')) return;
  _markLoaded('airports');
  fetch('/api/airports')
    .then(r => r.json())
    .then(d => {
      document.getElementById('ap-refreshed').textContent = 'Updated ' + d.now;

      // TSA wait times
      const tsaEl = document.getElementById('ap-tsa-content');
      if (!d.tsa_waits || !d.tsa_waits.length) {
        tsaEl.innerHTML = '<p style="color:#888;padding:12px 0;">No TSA wait time data available.</p>';
      } else {
        // Group by airport
        const byAirport = {};
        d.tsa_waits.forEach(w => {
          if (!byAirport[w.airport]) byAirport[w.airport] = [];
          byAirport[w.airport].push(w);
        });
        let html = '';
        for (const [apt, lanes] of Object.entries(byAirport)) {
          html += `<h3 style="margin:12px 0 6px;">${apt}</h3>
            <table><tr><th>Checkpoint</th><th>Lane</th><th>Status</th><th>Wait</th><th>Range</th><th>Gates</th><th>Last Updated</th></tr>`;
          lanes.forEach(w => {
            const openBadge = w.is_open
              ? '<span class="badge badge-yes">Open</span>'
              : '<span class="badge badge-no">Closed</span>';
            const waitColor = w.wait_min > 30 ? '#c62828' : w.wait_min > 15 ? '#e65100' : '#2e7d32';
            html += `<tr>
              <td><strong>${w.checkpoint}</strong></td>
              <td>${w.lane || '&mdash;'}</td>
              <td class="center">${openBadge}</td>
              <td class="center" style="color:${waitColor};font-weight:600;">${w.wait_min} min</td>
              <td class="center">${w.range}</td>
              <td class="center">${w.gates || '&mdash;'}</td>
              <td class="center"><small>${w.updated}</small></td></tr>`;
          });
          html += '</table>';
        }
        tsaEl.innerHTML = html;
      }

      // METAR table
      const metarEl = document.getElementById('ap-metar-content');
      document.getElementById('ap-metar-count').textContent = `(METAR \u2014 ${d.airports.length} stations)`;
      if (!d.airports.length) {
        metarEl.innerHTML = '<p class="no-data">No METAR data</p>';
      } else {
        let html = `<table><tr><th>ICAO</th><th>Airport</th><th>Cat</th>
          <th>Temp \u00b0F</th><th>Temp \u00b0C</th><th>Dewp \u00b0F</th><th>Dewp \u00b0C</th>
          <th>Wind Dir</th><th>Wind kt</th><th>Vis</th><th>Raw METAR</th><th>Obs Time (UTC)</th></tr>`;
        d.airports.forEach(ap => {
          html += `<tr><td><strong>${ap.icaoId}</strong></td><td>${ap.name}</td>
            <td class="center ${ap.flt_class}">${ap.fltCat}</td>
            <td class="center">${ap.temp_f}</td><td class="center">${ap.temp}</td>
            <td class="center">${ap.dewp_f}</td><td class="center">${ap.dewp}</td>
            <td class="center">${ap.wdir}</td><td class="center">${ap.wspd}</td>
            <td class="center">${ap.visib}</td><td><small>${ap.rawOb}</small></td>
            <td class="center">${ap.obsTime}</td></tr>`;
        });
        html += '</table>';
        metarEl.innerHTML = html;
      }
    })
    .catch(() => {
      document.getElementById('ap-refreshed').textContent = 'Failed to load \u2014 retrying...';
      _tabLoaded['airports'] = 0;
    });
}

// ---- Transcode ----
let _tcPollTimer = null;

function tcRefreshStatus() {
  fetch('/api/transcode/status')
    .then(r => r.json())
    .then(d => {
      document.getElementById('tc-counts').innerHTML =
        `Alerts: <strong>${d.total_alerts}</strong> total &nbsp;|&nbsp; `+
        `Missing audio: <strong style="color:${d.missing_alerts>0?'#c62828':'#2e7d32'}">${d.missing_alerts}</strong> `+
        `&nbsp;|&nbsp; Traffic: <strong>${d.total_traffic}</strong> total &nbsp;|&nbsp; `+
        `Missing audio: <strong style="color:${d.missing_traffic>0?'#c62828':'#2e7d32'}">${d.missing_traffic}</strong>`;
      const btn = document.getElementById('tc-btn');
      const st  = document.getElementById('tc-status');
      if (d.running) {
        btn.disabled = true;
        const phaseLabel = d.progress ? {nws:'NWS alerts',traffic:'traffic',complete:'finishing'}[d.progress.phase] || d.progress.phase : '';
        st.textContent = phaseLabel ? `Processing ${phaseLabel}\u2026` : 'Transcoding in progress\u2026';
        st.style.color = '#0077aa';
        if (!_tcPollTimer) _tcPollTimer = setInterval(tcRefreshStatus, 2000);
      } else {
        btn.disabled = false;
        st.textContent = d.missing_alerts === 0 && d.missing_traffic === 0 ? 'All alerts have audio \u2713' : '';
        st.style.color = '#2e7d32';
        if (_tcPollTimer) { clearInterval(_tcPollTimer); _tcPollTimer = null; }
      }
      _tcRenderProgress(d.progress, d.running);
    });
}

function _tcRenderProgress(p, running) {
  const el = document.getElementById('tc-progress');
  if (!p || !p.zones || Object.keys(p.zones).length === 0) {
    el.style.display = 'none';
    return;
  }
  el.style.display = '';
  const phaseMap = {nws: '\uD83D\uDD04 Processing NWS Alerts\u2026', traffic: '\uD83D\uDD04 Processing Traffic\u2026', complete: '\u2705 Complete'};
  const phaseLabel = phaseMap[p.phase] || p.phase || '';
  const startStr = p.started_at ? p.started_at.replace('T',' ').slice(0,19) + ' UTC' : '';
  const endStr   = p.completed_at ? ' \u00B7 Done: ' + p.completed_at.replace('T',' ').slice(0,19) + ' UTC' : '';
  let rows = '';
  for (const [zone, data] of Object.entries(p.zones)) {
    const nws = data.nws     || {done:0, skipped:0, failed:0};
    const tr  = data.traffic || {done:0, skipped:0, failed:0};
    const failNws = nws.failed > 0 ? ` <span style="color:#c62828">\u2717${nws.failed}</span>` : '';
    const failTr  = tr.failed  > 0 ? ` <span style="color:#c62828">\u2717${tr.failed}</span>`  : '';
    rows += `<tr style="border-top:1px solid #eee;">
      <td style="padding:4px 10px;font-weight:600;">${zone.replace(/_/g,' ')}</td>
      <td style="padding:4px 10px;text-align:center;">
        <span style="color:#2e7d32;font-weight:600;">${nws.done}</span> new
        <span style="color:#888;margin-left:6px;">+${nws.skipped} skip</span>${failNws}
      </td>
      <td style="padding:4px 10px;text-align:center;">
        <span style="color:#2e7d32;font-weight:600;">${tr.done}</span> new
        <span style="color:#888;margin-left:6px;">+${tr.skipped} skip</span>${failTr}
      </td>
    </tr>`;
  }
  el.innerHTML = `
    <div style="font-size:0.8rem;color:#555;margin-bottom:6px;">
      ${phaseLabel}&nbsp;&nbsp;<span style="color:#999;">Started: ${startStr}${endStr}</span>
    </div>
    <table style="font-size:0.8rem;border-collapse:collapse;width:100%;max-width:640px;background:#fafafa;border:1px solid #e0e0e0;border-radius:4px;">
      <thead><tr style="background:#f0f4f8;color:#444;">
        <th style="padding:5px 10px;text-align:left;">Zone</th>
        <th style="padding:5px 10px;text-align:center;">NWS Alerts</th>
        <th style="padding:5px 10px;text-align:center;">Traffic</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function tcRunNow() {
  document.getElementById('tc-btn').disabled = true;
  document.getElementById('tc-status').textContent = 'Starting\u2026';
  document.getElementById('tc-progress').style.display = 'none';
  fetch('/api/transcode/run', {method: 'POST'})
    .then(r => r.json())
    .then(d => {
      document.getElementById('tc-status').textContent = d.message;
      document.getElementById('tc-status').style.color = d.ok ? '#0077aa' : '#c62828';
      if (d.ok) {
        if (_tcPollTimer) clearInterval(_tcPollTimer);
        _tcPollTimer = setInterval(tcRefreshStatus, 2000);
      } else {
        document.getElementById('tc-btn').disabled = false;
      }
    })
    .catch(() => {
      document.getElementById('tc-status').textContent = 'Request failed';
      document.getElementById('tc-btn').disabled = false;
    });
}

// ---- Alert Audio Library ----
function loadZoneAudio(page, zone) {
  page = page || 1;
  zone = zone || 'all_florida';
  fetch(`/api/zone-audio?zone=${encodeURIComponent(zone)}&page=${page}`)
    .then(r => r.json())
    .then(d => {
      const container = document.getElementById('za-container');
      if (!container) return;
      const totalPages = Math.ceil(d.total / d.limit);
      // Populate zone selector if first load
      const sel = document.getElementById('za-zone-sel');
      if (sel && sel.options.length <= 1) {
        d.zones.forEach(z => {
          const opt = document.createElement('option');
          opt.value = z; opt.textContent = z.replace(/_/g,' ');
          if (z === zone) opt.selected = true;
          sel.appendChild(opt);
        });
      }
      document.getElementById('za-total').textContent = `${d.total.toLocaleString()} files`;
      const rows = d.items.map(f => {
        const dl = f.exists
          ? `<a href="/audio/download/${f.id}" download class="badge badge-yes" style="text-decoration:none;">&#8681; ${f.ext}</a>`
          : `<span class="badge badge-no">Missing</span>`;
        return `<tr><td><strong>${f.event}</strong></td><td>${f.alert_folder}</td>
          <td>${f.area_desc}</td><td class="center">${f.severity}</td>
          <td class="center">${f.generated_at}</td><td class="center">${dl}</td></tr>`;
      }).join('');
      container.innerHTML = `<table>
        <tr><th>Event</th><th>Category</th><th>Areas</th><th>Severity</th><th>Generated</th><th>Download</th></tr>
        ${rows || '<tr><td colspan="6" class="no-data">No audio files found</td></tr>'}
      </table>
      <div style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap;">
        ${page > 1 ? `<button class="btn-smtp-save" onclick="loadZoneAudio(${page-1}, document.getElementById('za-zone-sel').value)" style="padding:4px 12px;font-size:0.8rem;">&#8249; Prev</button>` : ''}
        <span style="font-size:0.8rem;color:#666;">Page ${page} of ${totalPages}</span>
        ${page < totalPages ? `<button class="btn-smtp-save" onclick="loadZoneAudio(${page+1}, document.getElementById('za-zone-sel').value)" style="padding:4px 12px;font-size:0.8rem;">Next &#8250;</button>` : ''}
      </div>`;
    })
    .catch(() => {
      const c = document.getElementById('za-container');
      if (c) c.innerHTML = '<p style="color:#c62828;">Failed to load audio library.</p>';
    });
}

// ── Reports tab ─────────────────────────────────────────────────────────────
function loadReports() {
  fetch('/api/reports/alert-events')
    .then(r => r.json())
    .then(events => {
      const sel = document.getElementById('rpt-event-filter');
      sel.innerHTML = '<option value="all">All event types</option>';
      (events || []).forEach(e => {
        const opt = document.createElement('option');
        opt.value = e; opt.textContent = e;
        sel.appendChild(opt);
      });
    }).catch(() => {});
  loadReportsList();
}

function loadReportsList() {
  fetch('/api/reports/list')
    .then(r => r.json())
    .then(files => {
      const tbody = document.getElementById('rpt-list-body');
      if (!files || files.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="color:#aaa;text-align:center;padding:14px;">No reports yet</td></tr>';
        return;
      }
      tbody.innerHTML = files.map(f => `
        <tr>
          <td>${f.filename}</td>
          <td>${f.size_kb} KB</td>
          <td><a href="/api/reports/download/${encodeURIComponent(f.filename)}"
               target="_blank" style="color:#0077aa;">&#11123; Download</a></td>
        </tr>`).join('');
    }).catch(() => {});
}

function generateReport() {
  const btn    = document.getElementById('rpt-gen-btn');
  const status = document.getElementById('rpt-status');
  const days   = document.getElementById('rpt-days').value;
  const zone   = document.getElementById('rpt-zone').value;
  const sev    = Array.from(document.querySelectorAll('.rpt-sev:checked'))
                       .map(c => c.value).join(',') || 'all';
  const evt    = document.getElementById('rpt-event-filter').value;
  const dfrom  = document.getElementById('rpt-date-from').value;
  const dto    = document.getElementById('rpt-date-to').value;
  const email  = document.getElementById('rpt-email').checked;

  btn.disabled = true;
  btn.textContent = 'Generating…';
  status.style.color = '#0077aa';
  status.textContent = 'Rendering PDF — this takes 30–60 seconds…';

  fetch('/api/reports/generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({days_back: parseInt(days), zone_label: zone,
                          severity_filter: sev, event_filter: evt,
                          date_from: dfrom, date_to: dto, send_email: email})
  })
  .then(r => r.json())
  .then(d => {
    btn.disabled = false;
    btn.textContent = 'Generate PDF Report';
    if (d.ok) {
      status.style.color = '#2e7d32';
      status.textContent = d.message;
      loadReportsList();
    } else {
      status.style.color = '#c62828';
      status.textContent = 'Error: ' + d.message;
    }
  })
  .catch(e => {
    btn.disabled = false;
    btn.textContent = 'Generate PDF Report';
    status.style.color = '#c62828';
    status.textContent = 'Request failed: ' + e;
  });
}

function toggleCustomDates() {
  const show = document.getElementById('rpt-days').value === '0';
  document.getElementById('rpt-custom-dates').style.display = show ? 'flex' : 'none';
}

// ── AI Agent tab ──────────────────────────────────────────────────────────────
function loadSituationReport() {
  fetch('/api/agent/situation')
    .then(r => r.json())
    .then(d => {
      const el = document.getElementById('sit-report-text');
      const meta = document.getElementById('sit-report-meta');
      if (d.ok && d.text) {
        el.textContent = d.text;
        meta.textContent = d.generated_at ? 'Generated: ' + d.generated_at : '';
      } else {
        el.innerHTML = '<span style="color:#aaa;">No situation report yet. Click "Generate New" to create one.</span>';
        meta.textContent = '';
      }
    })
    .catch(() => {
      document.getElementById('sit-report-text').innerHTML =
        '<span style="color:#c00;">Could not load situation report.</span>';
    });
}

function generateSituationReport() {
  const btn = document.getElementById('btn-gen-sit');
  btn.disabled = true;
  btn.textContent = '⏳ Generating…';
  document.getElementById('sit-report-text').innerHTML =
    '<span style="color:#aaa;">Running AI agent — this takes 15–30 seconds…</span>';
  fetch('/api/agent/situation/generate', {method: 'POST'})
    .then(r => r.json())
    .then(d => {
      btn.disabled = false;
      btn.textContent = '✨ Generate New';
      if (d.ok) {
        document.getElementById('sit-report-text').textContent = d.text;
        document.getElementById('sit-report-meta').textContent =
          'Generated just now · ' + d.iterations + ' tool call round(s)';
      } else {
        document.getElementById('sit-report-text').innerHTML =
          '<span style="color:#c00;">Error: ' + (d.error || 'unknown') + '</span>';
      }
    })
    .catch(e => {
      btn.disabled = false;
      btn.textContent = '✨ Generate New';
      document.getElementById('sit-report-text').innerHTML =
        '<span style="color:#c00;">Request failed: ' + e + '</span>';
    });
}

function sendAgentMessage() {
  const input = document.getElementById('agent-input');
  const msg   = input.value.trim();
  if (!msg) return;
  input.value = '';
  appendChatMsg(msg, 'user');
  const thinking = appendChatMsg('Thinking… (calling live data tools)', 'thinking');
  document.getElementById('btn-agent-send').disabled = true;

  fetch('/api/agent/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: msg})
  })
    .then(r => r.json())
    .then(d => {
      thinking.remove();
      document.getElementById('btn-agent-send').disabled = false;
      if (d.ok) {
        const bubble = appendChatMsg(d.response, 'agent');
        if (d.tool_calls && d.tool_calls.length) {
          const tools = d.tool_calls.map(t => t.tool).join(', ');
          const note = document.createElement('div');
          note.style.cssText = 'font-size:10px;color:#999;margin-top:4px;';
          note.textContent = '🔧 Tools used: ' + tools;
          bubble.appendChild(note);
        }
      } else {
        appendChatMsg('Error: ' + (d.error || 'unknown error'), 'agent');
      }
    })
    .catch(e => {
      thinking.remove();
      document.getElementById('btn-agent-send').disabled = false;
      appendChatMsg('Request failed: ' + e, 'agent');
    });
}

function quickAsk(msg) {
  document.getElementById('agent-input').value = msg;
  sendAgentMessage();
}

function appendChatMsg(text, type) {
  const history = document.getElementById('agent-chat-history');
  const div = document.createElement('div');
  div.className = 'chat-msg ' + (type === 'user' ? 'user-msg' :
                                  type === 'thinking' ? 'agent-thinking' : 'agent-msg');
  div.textContent = text;
  history.appendChild(div);
  history.scrollTop = history.scrollHeight;
  return div;
}
</script>

<!-- ===== REPORTS TAB ===== -->
<div id="tab-reports" class="tab-panel">
  <div style="max-width:960px;">

    <!-- RStudio link card -->
    <div class="cfg-card" style="background:#f0f7ff;border-color:#0077aa;margin-bottom:14px;">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
        <div>
          <h2 style="margin:0 0 4px;">R Studio &amp; Statistical Analysis</h2>
          <p style="margin:0;font-size:0.85rem;color:#555;">
            Open RStudio Server to edit the report template, build new charts, or run custom R analysis on the alert data.
          </p>
        </div>
        <a href="http://128.227.67.234:8787" target="_blank"
           style="background:#0077aa;color:#fff;padding:10px 22px;border-radius:5px;
                  text-decoration:none;font-weight:600;font-size:0.9rem;white-space:nowrap;">
          &#128196; Open RStudio Server
        </a>
      </div>
    </div>

    <!-- Generate report card -->
    <div class="cfg-card">
      <h2>Generate PDF Report</h2>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px;">

        <!-- Date range -->
        <div>
          <label style="font-size:.85rem;font-weight:600;color:#333;">Report Period</label><br>
          <select id="rpt-days" onchange="toggleCustomDates()"
                  style="width:100%;padding:7px;border:1px solid #ccc;border-radius:4px;margin-top:4px;font-size:.9rem;">
            <option value="1">Last 24 hours</option>
            <option value="7" selected>Last 7 days</option>
            <option value="14">Last 14 days</option>
            <option value="30">Last 30 days</option>
            <option value="0">Custom date range…</option>
          </select>
          <div id="rpt-custom-dates" style="display:none;gap:8px;margin-top:8px;align-items:center;">
            <input type="date" id="rpt-date-from"
                   style="flex:1;padding:6px;border:1px solid #ccc;border-radius:4px;font-size:.85rem;">
            <span style="color:#888;">to</span>
            <input type="date" id="rpt-date-to"
                   style="flex:1;padding:6px;border:1px solid #ccc;border-radius:4px;font-size:.85rem;">
          </div>
        </div>

        <!-- Zone -->
        <div>
          <label style="font-size:.85rem;font-weight:600;color:#333;">Zone</label><br>
          <select id="rpt-zone"
                  style="width:100%;padding:7px;border:1px solid #ccc;border-radius:4px;margin-top:4px;font-size:.9rem;">
            <option>All Florida</option>
            <option>North Florida</option>
            <option>Central Florida</option>
            <option>South Florida</option>
            <option>Alachua County</option>
          </select>
        </div>

        <!-- Severity filter -->
        <div>
          <label style="font-size:.85rem;font-weight:600;color:#333;">Severity</label>
          <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:10px;">
            <label style="font-size:.85rem;"><input type="checkbox" class="rpt-sev" value="Extreme" checked> Extreme</label>
            <label style="font-size:.85rem;"><input type="checkbox" class="rpt-sev" value="Severe" checked> Severe</label>
            <label style="font-size:.85rem;"><input type="checkbox" class="rpt-sev" value="Moderate" checked> Moderate</label>
            <label style="font-size:.85rem;"><input type="checkbox" class="rpt-sev" value="Minor" checked> Minor</label>
          </div>
        </div>

        <!-- Event type filter -->
        <div>
          <label style="font-size:.85rem;font-weight:600;color:#333;">Event Type</label><br>
          <select id="rpt-event-filter"
                  style="width:100%;padding:7px;border:1px solid #ccc;border-radius:4px;margin-top:4px;font-size:.9rem;">
            <option value="all">All event types</option>
          </select>
        </div>
      </div>

      <!-- Email + generate -->
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;border-top:1px solid #eee;padding-top:12px;">
        <label style="font-size:.9rem;">
          <input type="checkbox" id="rpt-email" checked>
          Email report to <strong>lawrence.bornace@ufl.edu</strong>
        </label>
        <button id="rpt-gen-btn" onclick="generateReport()"
                style="background:#0077aa;color:#fff;border:none;padding:9px 24px;
                       border-radius:5px;font-size:.95rem;font-weight:600;cursor:pointer;">
          Generate PDF Report
        </button>
      </div>
      <p id="rpt-status" style="margin:10px 0 0;font-size:.9rem;min-height:1.2em;"></p>
    </div>

    <!-- Recent reports -->
    <div class="cfg-card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
        <h2 style="margin:0;">Recent Reports</h2>
        <button onclick="loadReportsList()"
                style="background:none;border:1px solid #0077aa;color:#0077aa;
                       padding:5px 14px;border-radius:4px;cursor:pointer;font-size:.85rem;">
          Refresh
        </button>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:.88rem;">
        <thead>
          <tr style="background:#f5f5f5;">
            <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #ddd;">Filename</th>
            <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #ddd;">Size</th>
            <th style="text-align:left;padding:8px 10px;border-bottom:2px solid #ddd;">Download</th>
          </tr>
        </thead>
        <tbody id="rpt-list-body">
          <tr><td colspan="3" style="color:#aaa;text-align:center;padding:14px;">Loading…</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Scheduled info -->
    <div class="cfg-card" style="background:#f9fdf9;border-color:#2e7d32;">
      <h2 style="color:#2e7d32;">Scheduled Reports</h2>
      <p style="margin:0 0 6px;font-size:.9rem;">
        &#9200; A daily 7-day report is auto-generated and emailed every day at <strong>6:00 AM</strong>.
      </p>
      <p style="margin:0;font-size:.85rem;color:#555;">
        Reports are saved to <code>/home/ufuser/Fpren-main/reports/output/</code> &mdash;
        accessible in <a href="http://128.227.67.234:8787" target="_blank" style="color:#0077aa;">RStudio Server</a>
        or the <a href="http://128.227.67.234:3838/fpren" target="_blank" style="color:#0077aa;">Shiny Dashboard</a>.
      </p>
    </div>

  </div>
</div>


<!-- ===== ZONES TAB ===== -->
<div id="tab-zones" class="tab-panel">
  <div style="max-width:960px;">
    <div class="cfg-card">
      <h2>Zones</h2>
      <table class="cfg-table"><thead><tr><th>Zone</th><th>Type</th><th>Counties</th><th>Max Age</th><th>Max Files</th></tr></thead>
      <tbody id="zones-tbody"><tr><td colspan="5">Loading...</td></tr></tbody></table>
    </div>
  </div>
</div>

<div id="tab-upload" class="tab-panel">
  <div style="max-width:960px;">
    <div class="cfg-card">
      <h2>&#8679; Upload Audio Content</h2>
      <div style="margin-bottom:16px;">
        <label style="color:#555;font-size:0.9rem;">Target Folder:</label>
        <select id="upload-folder" style="margin-left:8px;padding:6px;border:1px solid #ccc;border-radius:4px;">
          <option value="top_of_hour">Top of Hour</option>
          <option value="imaging">Imaging / Sweepers</option>
          <option value="music">Music</option>
          <option value="educational">Educational</option>
          <option value="weather_report">Weather Report</option>
        </select>
      </div>
      <div id="upload-drop" style="border:2px dashed #ccc;border-radius:8px;padding:40px;text-align:center;cursor:pointer;margin-bottom:16px;background:#fafafa;">
        <p style="font-size:1.1rem;color:#555;">&#127925; Drag and drop MP3/WAV files here</p>
        <p style="color:#aaa;font-size:0.85rem;">or click to browse</p>
        <input type="file" id="upload-input" multiple accept=".mp3,.wav,.ogg,.m4a" style="display:none;">
      </div>
      <div id="upload-status" style="margin-bottom:16px;"></div>
      <h3 style="margin-bottom:8px;color:#444;">Files in folder:</h3>
      <div id="upload-file-list">Loading...</div>
    </div>
  </div>
</div>
</body>
</html>
"""

# -------------------- ROUTES ----------------------

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FPREN | Florida Public Radio Emergency Network</title>
  <style>
    body { background:#1a1f24; display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; font-family:Arial,sans-serif; }
    .card { background:#212529; border:1px solid #343a40; border-radius:8px; padding:40px; width:340px; }
    h1 { color:#0dcaf0; text-align:center; margin-bottom:8px; font-size:1.4rem; }
    p { color:#adb5bd; text-align:center; margin-bottom:24px; font-size:0.9rem; }
    label { color:#adb5bd; font-size:0.85rem; display:block; margin-bottom:4px; }
    input { width:100%; padding:10px; margin-bottom:16px; background:#343a40; border:1px solid #495057; color:#fff; border-radius:4px; font-size:0.95rem; box-sizing:border-box; }
    button { width:100%; padding:12px; background:#0dcaf0; color:#111; border:none; border-radius:4px; font-size:1rem; font-weight:bold; cursor:pointer; }
    button:hover { background:#0bb8d4; }
    .error { background:#3d1515; border:1px solid #a00; color:#ff8080; padding:10px; border-radius:4px; margin-bottom:16px; font-size:0.9rem; }
    .logo { text-align:center; color:#fff; font-size:0.75rem; margin-bottom:20px; line-height:1.4; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo"><strong style="font-size:1.1rem;color:#0dcaf0;">FPREN</strong><br>Florida Public Radio Emergency Network</div>
    <h1>Sign In</h1>
    <p>Florida Public Radio Emergency Network</p>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="POST">
      <label>Username</label>
      <input type="text" name="username" autofocus required>
      <label>Password</label>
      <input type="password" name="password" required>
      <button type="submit">Sign In</button>
    </form>
  </div>
</body>
</html>
"""

@app.route("/login", methods=["GET","POST"])
def login_page():
    from flask_login import current_user
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").encode()
        from pymongo import MongoClient
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        doc = client["weather_rss"]["users"].find_one({"username": username, "active": True})
        client.close()
        if doc and bcrypt.checkpw(password, doc["password"].encode()):
            login_user(User(doc), remember=True)
            return redirect(url_for('dashboard'))
        error = "Invalid username or password."
    from flask import render_template_string
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
@login_required
def logout_route():
    logout_user()
    return redirect(url_for("login_page"))

@app.route("/")
@login_required
def dashboard():
    html = render_template_string(HTML_TEMPLATE, zones=AVAILABLE_ZONES)
    return html.encode("utf-8", "replace").decode("utf-8")

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

@app.route("/api/streams/<stream_id>/stop", methods=["POST"])
def api_stream_stop(stream_id):
    import base64, urllib.error
    stream = next((s for s in STREAMS if s["id"] == stream_id), None)
    if not stream:
        return jsonify({"ok": False, "message": "Unknown stream"}), 404
    mount = stream["mount"]
    try:
        url = f"http://localhost:8000/admin/killsource?mount={mount}"
        req = _ureq.Request(url)
        req.add_header("Authorization",
                       "Basic " + base64.b64encode(b"admin:hackme").decode())
        with _ureq.urlopen(req, timeout=5) as resp:
            resp.read()
        return jsonify({"ok": True, "message": f"Source for {mount} stopped"})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if "client not found" in body.lower():
            return jsonify({"ok": False, "message": f"{mount} has no active source — already offline?"})
        return jsonify({"ok": False, "message": f"Icecast error {e.code}: {body[:120]}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@app.route("/api/streams/start-engine", methods=["POST"])
def api_stream_start_engine():
    import subprocess
    try:
        # Ensure Icecast is up first
        subprocess.run(["sudo", "systemctl", "start", "icecast2"],
                       capture_output=True, text=True, timeout=10)
        result = subprocess.run(
            ["sudo", "systemctl", "start", "fpren-station-engine"],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode == 0:
            return jsonify({"ok": True, "message": "Broadcast engine starting\u2026"})
        return jsonify({"ok": False, "message": result.stderr.strip() or "Start failed"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@app.route("/api/streams/restart-engine", methods=["POST"])
def api_stream_restart_engine():
    import subprocess
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "fpren-station-engine"],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode == 0:
            return jsonify({"ok": True, "message": "Broadcast engine restarting\u2026"})
        return jsonify({"ok": False, "message": result.stderr.strip() or "Restart failed"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

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
        msg["Subject"] = "FPREN Dashboard — SMTP Test"
        msg["From"]    = mail_from
        msg["To"]      = mail_to
        msg.set_content(
            "This is a test email from the FPREN Alerts Dashboard.\n"
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

# -------------------- ICECAST API ---------------
@app.route("/api/icecast")
def api_icecast():
    import xml.etree.ElementTree as ET
    results = []
    for s in STREAMS:
        entry = {
            "id": s["id"], "label": s["label"],
            "port": s["port"], "mount": s["mount"],
            "live": False, "listeners": 0,
            "bitrate": None, "format": None, "title": None,
        }
        try:
            url = "http://localhost:8000/admin/stats"
            req = _ureq.Request(url)
            import base64
            creds = base64.b64encode(b"admin:hackme").decode()
            req.add_header("Authorization", f"Basic {creds}")
            with _ureq.urlopen(req, timeout=3) as resp:
                tree = ET.fromstring(resp.read())
            for src in tree.findall(".//source"):
                if src.get("mount") == s["mount"]:
                    entry["live"]      = True
                    entry["listeners"] = int(src.findtext("listeners") or 0)
                    entry["format"]    = src.findtext("server_type")
                    entry["title"]     = src.findtext("title") or src.findtext("server_name")
                    bitrate = src.findtext("bitrate")
                    if not bitrate:
                        audio_info = src.findtext("audio_info") or ""
                        for part in audio_info.split(";"):
                            if "bitrate" in part.lower():
                                bitrate = part.split("=")[-1].strip()
                                break
                    entry["bitrate"] = bitrate
                    break
        except Exception:
            pass
        results.append(entry)
    return jsonify(results)

# -------------------- PLAYLIST API --------------
PLAYLISTS_DIR       = "/home/ufuser/Fpren-main/weather_station/playlists"
STREAM_PLAYLISTS_FILE = "/home/ufuser/Fpren-main/weather_station/config/stream_playlists.json"
NOW_PLAYING_FILE    = "/tmp/fpren_now_playing.json"

def _load_stream_playlists():
    try:
        with open(STREAM_PLAYLISTS_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

def _save_stream_playlists(data):
    with open(STREAM_PLAYLISTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


@app.route("/api/playlist")
def api_playlist():
    sp = _load_stream_playlists()
    active_file = sp.get("stream_8000", "normal.json")
    available = []
    for fname in sorted(os.listdir(PLAYLISTS_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(PLAYLISTS_DIR, fname)) as f:
                pl = json.load(f)
            available.append({
                "file":        fname,
                "name":        pl.get("name", fname),
                "description": pl.get("description", ""),
                "slots":       pl.get("slots", []),
            })
        except (OSError, json.JSONDecodeError):
            pass
    now_playing = None
    try:
        with open(NOW_PLAYING_FILE) as f:
            now_playing = json.load(f)
    except (FileNotFoundError, ValueError):
        pass
    streams_out = []
    for s in STREAMS:
        streams_out.append({
            "id":    s["id"],
            "label": s["label"],
            "port":  s["port"],
            "active": sp.get(s["id"], "normal.json"),
            "muted":  bool(sp.get(f"{s['id']}_muted", False)),
        })
    return jsonify({
        "active":      active_file,
        "available":   available,
        "now_playing": now_playing,
        "streams":     streams_out,
    })

@app.route("/api/playlist/mute/toggle", methods=["POST"])
def api_playlist_mute_toggle():
    data = request.get_json(silent=True) or {}
    stream_id = data.get("stream_id", "").strip()
    if not stream_id:
        return jsonify({"ok": False, "message": "Missing stream_id"}), 400
    sp = _load_stream_playlists()
    muted = not bool(sp.get(f"{stream_id}_muted", False))
    sp[f"{stream_id}_muted"] = muted
    _save_stream_playlists(sp)
    return jsonify({"ok": True, "muted": muted,
                    "message": "Muted" if muted else "Unmuted"})

@app.route("/api/playlist/<path:filename>/slots", methods=["POST"])
def api_playlist_slots(filename):
    data = request.get_json(silent=True) or {}
    slots = data.get("slots")
    if slots is None:
        return jsonify({"ok": False, "message": "Missing slots"}), 400
    filepath = os.path.join(PLAYLISTS_DIR, filename)
    if not os.path.isfile(filepath):
        return jsonify({"ok": False, "message": f"Playlist {filename} not found"}), 404
    try:
        with open(filepath) as f:
            pl = json.load(f)
        pl["slots"] = slots
        with open(filepath, "w") as f:
            json.dump(pl, f, indent=2)
        return jsonify({"ok": True, "message": f"Saved {len(slots)} slots to {filename}"})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500

@app.route("/api/playlist/assign", methods=["POST"])
def api_playlist_assign():
    data = request.get_json(silent=True) or {}
    stream_id = data.get("stream_id", "").strip()
    file      = data.get("file", "").strip()
    if not stream_id or not file:
        return jsonify({"ok": False, "message": "Missing stream_id or file"}), 400
    if not os.path.isfile(os.path.join(PLAYLISTS_DIR, file)):
        return jsonify({"ok": False, "message": f"Playlist {file} not found"}), 404
    sp = _load_stream_playlists()
    sp[stream_id] = file
    _save_stream_playlists(sp)
    return jsonify({"ok": True, "message": f"Assigned {file} to {stream_id}"})

# -------------------- ALERT WAV DOWNLOAD --------
_ALL_FLORIDA_ZONE = "/home/ufuser/Fpren-main/weather_station/audio/zones/all_florida"
_AUDIO_SEARCH_ROOTS = [
    "/home/ufuser/Fpren-main/weather_station/audio/zones",
    "/home/ufuser/Fpren-main/weather_station/audio/alert_tones",
    "/home/ufuser/Fpren-main/audio_playlist/alerts",
    "/home/ufuser/Fpren-main/wav_output",
]

def _resolve_audio(alert_id: str = None, wav_path: str = None):
    """Return a real filesystem path for an alert audio file (MP3 or WAV)."""
    # 1. Direct path on disk
    if wav_path and os.path.isfile(wav_path):
        return wav_path
    # 2. zone_alert_wavs lookup by source_id
    if alert_id:
        doc = zone_wavs_col.find_one(
            {"source_id": alert_id, "zone": "all_florida"}) or \
            zone_wavs_col.find_one({"source_id": alert_id})
        if doc:
            p = doc.get("wav_path", "")
            if p and os.path.isfile(p):
                return p
    # 3. Stem search (handles both .mp3 and .wav)
    stem = None
    if alert_id:
        stem = re.sub(r'[:.]', '_', alert_id)
    elif wav_path:
        stem = os.path.splitext(os.path.basename(wav_path))[0]
    if stem:
        for root in _AUDIO_SEARCH_ROOTS:
            for dirpath, _, files in os.walk(root):
                for f in files:
                    if os.path.splitext(f)[0] == stem and \
                            f.endswith((".mp3", ".wav")):
                        return os.path.join(dirpath, f)
    return None

def _audio_mimetype(path: str) -> str:
    return "audio/mpeg" if path.endswith(".mp3") else "audio/wav"

@app.route("/alerts/<path:alert_id>/wav")
def alert_wav(alert_id):
    resolved = _resolve_audio(alert_id=alert_id)
    if not resolved:
        # Also try looking up wav_path from nws_alerts
        doc = alerts_col.find_one({"alert_id": alert_id})
        if doc:
            resolved = _resolve_audio(alert_id, doc.get("wav_path"))
    if not resolved:
        abort(404)
    filename = os.path.basename(resolved)
    return send_file(resolved, mimetype=_audio_mimetype(resolved),
                     as_attachment=True, download_name=filename)

@app.route("/audio/download/<doc_id>")
def audio_download(doc_id):
    """Serve an audio file by zone_alert_wavs _id."""
    from bson import ObjectId
    try:
        doc = zone_wavs_col.find_one({"_id": ObjectId(doc_id)})
    except Exception:
        abort(400)
    if not doc:
        abort(404)
    path = doc.get("wav_path", "")
    if not path or not os.path.isfile(path):
        abort(404)
    filename = os.path.basename(path)
    return send_file(path, mimetype=_audio_mimetype(path),
                     as_attachment=True, download_name=filename)

@app.route("/api/zone-audio")
def api_zone_audio():
    """List alert audio files from zone_alert_wavs, paginated."""
    zone      = request.args.get("zone", "all_florida")
    page      = max(1, int(request.args.get("page", 1)))
    limit     = 50
    skip      = (page - 1) * limit
    query     = {"zone": zone} if zone else {}
    total     = zone_wavs_col.count_documents(query)
    docs      = list(zone_wavs_col.find(
        query, sort=[("generated_at", -1)], skip=skip, limit=limit))
    zones_all = zone_wavs_col.distinct("zone")
    items = []
    for d in docs:
        path = d.get("wav_path", "")
        gen  = d.get("generated_at")
        items.append({
            "id":           str(d["_id"]),
            "event":        d.get("event", "—"),
            "zone":         d.get("zone", ""),
            "alert_folder": d.get("alert_folder", ""),
            "area_desc":    d.get("area_desc", "—"),
            "severity":     d.get("severity", "—"),
            "generated_at": gen.strftime("%Y-%m-%d %H:%M") if hasattr(gen, "strftime") else str(gen or "—"),
            "filename":     os.path.basename(path),
            "ext":          os.path.splitext(path)[1].lstrip(".").upper() or "?",
            "exists":       os.path.isfile(path),
        })
    return jsonify({"total": total, "page": page, "limit": limit,
                    "zones": sorted(zones_all), "items": items})

# -------------------- TRANSCODE API -------------
_VENV_PYTHON   = "/home/ufuser/Fpren-main/venv/bin/python3"
_PROJECT_ROOT  = "/home/ufuser/Fpren-main"
_transcode_lock = __import__("threading").Lock()
_transcode_running = {"pid": None}

@app.route("/api/transcode/status")
def api_transcode_status():
    import subprocess, json as _json
    total   = alerts_col.count_documents({})
    traffic = fl_traffic_col.count_documents({})
    existing_ids = set(zone_wavs_col.distinct("source_id"))
    missing_alerts  = alerts_col.count_documents(
        {"alert_id": {"$nin": list(existing_ids)}})
    missing_traffic = fl_traffic_col.count_documents(
        {"incident_id": {"$nin": list(existing_ids)}})
    # Check if a run_once process is still alive
    pid = _transcode_running.get("pid")
    running = False
    if pid:
        try:
            running = subprocess.run(["kill", "-0", str(pid)],
                                     capture_output=True).returncode == 0
        except Exception:
            pass
    # Read per-zone progress written by zone_alert_tts run_once
    progress = None
    try:
        with open("/tmp/fpren_transcode_progress.json") as _f:
            progress = _json.load(_f)
    except Exception:
        pass
    return jsonify({
        "total_alerts":      total,
        "total_traffic":     traffic,
        "missing_alerts":    missing_alerts,
        "missing_traffic":   missing_traffic,
        "running":           running,
        "progress":          progress,
    })

@app.route("/api/transcode/run", methods=["POST"])
def api_transcode_run():
    import subprocess
    with _transcode_lock:
        pid = _transcode_running.get("pid")
        if pid:
            try:
                alive = subprocess.run(["kill", "-0", str(pid)],
                                       capture_output=True).returncode == 0
            except Exception:
                alive = False
            if alive:
                return jsonify({"ok": False, "message": "Transcoding already running"}), 409
        try:
            proc = subprocess.Popen(
                [_VENV_PYTHON, "-m",
                 "weather_station.services.zone_alert_tts", "--once"],
                cwd=_PROJECT_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _transcode_running["pid"] = proc.pid
            return jsonify({"ok": True,
                            "message": f"Transcoding started (PID {proc.pid})"})
        except Exception as e:
            return jsonify({"ok": False, "message": str(e)}), 500

# -------------------- STREAM ALERT TEST ---------
@app.route("/api/stream-alert/test", methods=["POST"])
def api_stream_alert_test():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ok, msg = _send_stream_alert_email(
        subject=f"FPREN Stream Alert TEST — Port 8000 ({now})",
        body=(
            f"This is a TEST alert for stream port 8000 (All Florida / {_MONITOR_STREAM['mount']}).\n\n"
            f"If you received this, stream-down email alerts are working correctly.\n"
            f"Sent at: {now}\n"
            f"Dashboard: http://10.242.41.77:5000\n"
        ),
    )
    return jsonify({"ok": ok, "message": msg})

# -------------------- DATA TAB API --------------
@app.route("/api/data-tab")
def api_data_tab():
    now = datetime.now(timezone.utc)

    # RSS feed status
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

    # NWS alerts — bulk-load audio docs in one query to avoid N+1
    alert_docs = list(alerts_col.find({}, sort=[("fetched_at", -1)], limit=ALERTS_LIMIT))
    alert_ids  = [str(a.get("alert_id", "")) for a in alert_docs if a.get("alert_id")]
    # One query per zone preference: all_florida first, then any zone
    wav_by_id  = {}
    for doc in zone_wavs_col.find({"source_id": {"$in": alert_ids}}):
        sid = doc["source_id"]
        # Keep all_florida hit if available, otherwise keep first found
        if sid not in wav_by_id or doc.get("zone") == "all_florida":
            wav_by_id[sid] = doc

    alerts = []
    for a in alert_docs:
        sent = a.get("sent", "")
        if isinstance(sent, datetime):
            sent = sent.strftime("%Y-%m-%d %H:%M")
        elif isinstance(sent, str) and sent:
            try:
                sent = datetime.fromisoformat(sent).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        severity  = a.get("severity", "")
        nws_id    = str(a.get("alert_id", ""))
        audio_doc = wav_by_id.get(nws_id)
        audio_id  = str(audio_doc["_id"]) if audio_doc else None
        alerts.append({
            "event":         a.get("event", "—"),
            "headline":      a.get("headline", "—"),
            "severity":      severity,
            "area_desc":     a.get("area_desc", "—"),
            "sender":        a.get("sender", "—"),
            "sent":          sent or "—",
            "tts_generated": bool(audio_doc),
            "sev_class":     SEVERITY_CLASS.get(severity, ""),
            "audio_id":      audio_id,
            "audio_ext":     os.path.splitext(audio_doc.get("wav_path", ""))[1].lstrip(".").upper() if audio_doc else None,
        })

    # FL Traffic
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

    # School closings
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

    # Now playing
    now_playing = None
    try:
        with open("/tmp/fpren_now_playing.json") as f:
            now_playing = json.load(f)
    except (FileNotFoundError, ValueError):
        pass

    return jsonify({
        "now":         now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "now_playing": now_playing,
        "alerts":      alerts,
        "traffic":     traffic,
        "school":      school,
        "feeds":       feeds,
    })

# -------------------- AIRPORTS TAB ---------------

# Florida airports with public TSA wait-time APIs
_MCO_WAIT_URL = "https://api.goaa.aero/wait-times/checkpoint/MCO"
_MIA_WAIT_URL = "https://waittime.api.aero/waittime/v2/current/MIA"

# FL airport display names for METAR table
_AIRPORT_NAMES = {
    "KGNV": "Gainesville Regional",
    "KOCF": "Ocala International",
    "KPAK": "Palatka",
    "KJAX": "Jacksonville International",
    "KTLH": "Tallahassee International",
    "KPNS": "Pensacola International",
    "KECP": "Northwest FL Beaches Int'l",
    "KMCO": "Orlando International",
    "KDAB": "Daytona Beach International",
    "KTPA": "Tampa International",
    "KSRQ": "Sarasota Bradenton Int'l",
    "KLAL": "Lakeland Linder Int'l",
    "KRSW": "Southwest FL International",
    "KFLL": "Fort Lauderdale-Hollywood",
    "KMIA": "Miami International",
    "KPBI": "Palm Beach International",
    "KEYW": "Key West International",
    "KSPG": "St. Pete-Clearwater Int'l",
    "KAPF": "Naples Municipal",
}


def _fetch_mco_waits() -> list:
    """Fetch MCO TSA wait times. Returns list of lane dicts or [] on error."""
    try:
        req = _ureq.Request(
            _MCO_WAIT_URL,
            headers={
                "Api-Key": "8eaac7209c824616a8fe58d22268cd59",
                "Api-Version": "140",
                "Referer": "https://flymco.com/",
                "User-Agent": "FPREN/1.0",
            },
        )
        with _ureq.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        lanes = data.get("data", {}).get("wait_times", [])
        result = []
        for lane in lanes:
            mins = round(lane.get("waitSeconds", 0) / 60)
            min_m = round(lane.get("minWaitSeconds", 0) / 60)
            max_m = round(lane.get("maxWaitSeconds", 0) / 60)
            attrs = lane.get("attributes", {})
            result.append({
                "airport":    "MCO",
                "checkpoint": lane.get("name", ""),
                "lane":       lane.get("lane", ""),
                "is_open":    lane.get("isOpen", False),
                "wait_min":   mins,
                "range":      f"{min_m}–{max_m} min",
                "gates":      f"{attrs.get('minGate','')}–{attrs.get('maxGate','')}".strip("–"),
                "updated":    lane.get("lastUpdatedTimestamp", ""),
            })
        return result
    except Exception:
        return []


def _fetch_mia_waits() -> list:
    """Fetch MIA TSA wait times. Returns list of queue dicts or [] on error."""
    try:
        req = _ureq.Request(
            _MIA_WAIT_URL,
            headers={
                "x-apikey": "5d0cacea6e41416fdcde0c5c5a19d867",
                "Origin": "https://www.miami-airport.com",
                "User-Agent": "FPREN/1.0",
            },
        )
        with _ureq.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        queues = data if isinstance(data, list) else data.get("current", [])
        result = []
        for q in queues:
            result.append({
                "airport":    "MIA",
                "checkpoint": q.get("queueName", ""),
                "lane":       "",
                "is_open":    q.get("status", "").upper() == "OPEN",
                "wait_min":   q.get("projectedWaitTime", 0),
                "range":      f"{q.get('projectedMinWaitMinutes',0)}–{q.get('projectedMaxWaitMinutes',0)} min",
                "gates":      "",
                "updated":    q.get("localTime", q.get("time", "")),
            })
        return result
    except Exception:
        return []


@app.route("/api/airports")
def api_airports():
    def to_f(c):
        try:
            return round(float(c) * 9 / 5 + 32, 1)
        except (TypeError, ValueError):
            return ""

    # METAR data
    airports = []
    for ap in airport_metar_col.find({}, sort=[("icaoId", 1)]):
        flt_cat = ap.get("fltCat", "")
        obs = ap.get("obsTime", "")
        if isinstance(obs, str) and "T" in obs:
            try:
                dt = datetime.fromisoformat(obs)
                obs = dt.strftime("%m-%d %H:%MZ")
            except ValueError:
                pass
        temp_c = ap.get("temp", "")
        dewp_c = ap.get("dewp", "")
        icao   = ap.get("icaoId", "")
        airports.append({
            "icaoId":    icao,
            "name":      ap.get("name", "") or _AIRPORT_NAMES.get(icao, icao),
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

    # TSA wait times
    tsa_waits = _fetch_mco_waits() + _fetch_mia_waits()

    return jsonify({
        "now":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "airports":  airports,
        "tsa_waits": tsa_waits,
    })


# -------------------- AI ENDPOINTS --------------

_LITELLM_BASE_URL = os.environ.get("UF_LITELLM_BASE_URL", "https://api.ai.it.ufl.edu")
_LITELLM_API_KEY  = os.environ.get("UF_LITELLM_API_KEY", "")
_LITELLM_MODEL    = os.environ.get("UF_LITELLM_MODEL", "gpt-4o-mini")

_REWRITE_SYSTEM = (
    "You are a professional emergency broadcast radio announcer for FPREN, "
    "the Florida Public Radio Emergency Network. "
    "Rewrite the provided NWS alert text as a concise, clear, spoken radio script. "
    "Rules: write for the ear (spell out abbreviations, plain language), start directly "
    "with the alert, keep it under 120 words, do not add new information, end with a "
    "clear call-to-action. Output plain text only."
)

_BROADCAST_SYSTEM = (
    "You are a professional weather radio announcer for FPREN covering North Florida. "
    "Generate a concise spoken broadcast summary from the provided weather and alert data. "
    "Lead with active alerts, then current conditions, then a brief outlook. "
    "Keep it under 180 words. Output plain text only."
)


def _ai_chat(prompt: str, system: str, max_tokens: int = 400) -> tuple[bool, str]:
    """Call LiteLLM and return (ok, text). Returns (False, error_msg) on failure."""
    if not _LITELLM_API_KEY:
        return False, "UF_LITELLM_API_KEY is not configured."
    try:
        from openai import OpenAI as _OpenAI
        client = _OpenAI(base_url=_LITELLM_BASE_URL, api_key=_LITELLM_API_KEY)
        resp = client.chat.completions.create(
            model=_LITELLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=max_tokens,
        )
        return True, resp.choices[0].message.content.strip()
    except Exception as exc:
        return False, str(exc)


@app.route("/api/ai/rewrite-alert", methods=["POST"])
def api_ai_rewrite_alert():
    """Rewrite a raw NWS alert into a broadcast-ready radio script.

    POST body: { "headline": str, "area": str, "description": str }
    """
    data    = request.get_json(silent=True) or {}
    headline = data.get("headline", "").strip()
    area     = data.get("area", "").strip()
    desc     = data.get("description", "").strip()
    if not headline:
        return jsonify({"ok": False, "message": "Missing headline"}), 400
    prompt = (
        f"NWS Alert:\nHeadline: {headline}\nAffected areas: {area}\n"
        f"Description: {desc[:800]}"
    )
    ok, text = _ai_chat(prompt, _REWRITE_SYSTEM, max_tokens=300)
    return jsonify({"ok": ok, "script": text if ok else "", "message": text if not ok else "OK"})


@app.route("/api/ai/broadcast", methods=["POST"])
def api_ai_broadcast():
    """Generate a full weather broadcast script from current DB data.

    POST body: optional { "max_alerts": int, "max_obs": int }
    Returns: { "ok": bool, "script": str, "message": str }
    """
    data       = request.get_json(silent=True) or {}
    max_alerts = int(data.get("max_alerts", 5))
    max_obs    = int(data.get("max_obs", 5))

    # Pull live alerts
    alert_rows = list(alerts_col.find({}, sort=[("fetched_at", -1)], limit=max_alerts))
    alert_lines = "\n".join(
        f"- [{a.get('severity','').upper()}] {a.get('event','')}: {a.get('headline','')}"
        for a in alert_rows
    ) or "None active"

    # Pull current METAR observations
    obs_rows = list(airport_metar_col.find({}, sort=[("icaoId", 1)], limit=max_obs))
    def _to_f(c):
        try: return round(float(c) * 9/5 + 32, 1)
        except: return "?"
    obs_lines = "\n".join(
        f"- {o.get('icaoId','')}: {_to_f(o.get('temp',''))}°F, "
        f"wind {o.get('wspd','?')}kt, vis {o.get('visib','?')}sm"
        for o in obs_rows
    ) or "No observations"

    prompt = f"Active NWS Alerts:\n{alert_lines}\n\nCurrent Observations:\n{obs_lines}"
    ok, text = _ai_chat(prompt, _BROADCAST_SYSTEM, max_tokens=400)
    return jsonify({"ok": ok, "script": text if ok else "", "message": text if not ok else "OK"})


# -------------------- REPORTS API ---------------
REPORTS_DIR   = "/home/ufuser/Fpren-main/reports/output"
REPORTS_RSCRIPT = "/home/ufuser/Fpren-main/reports/generate_and_email.R"
_report_lock  = __import__("threading").Lock()
_report_running = {"value": False}


@app.route("/api/reports/alert-events")
def api_report_alert_events():
    """Return distinct NWS alert event types for the filter dropdown."""
    try:
        events = sorted(alerts_col.distinct("event"))
        return jsonify(events)
    except Exception:
        return jsonify([])


@app.route("/api/reports/list")
def api_report_list():
    """Return list of generated PDF reports sorted newest first."""
    import glob
    os.makedirs(REPORTS_DIR, exist_ok=True)
    files = sorted(
        glob.glob(os.path.join(REPORTS_DIR, "*.pdf")),
        key=os.path.getmtime, reverse=True
    )
    result = []
    for f in files[:20]:
        result.append({
            "filename": os.path.basename(f),
            "size_kb":  round(os.path.getsize(f) / 1024, 1),
            "mtime":    os.path.getmtime(f),
        })
    return jsonify(result)


@app.route("/api/reports/download/<path:filename>")
def api_report_download(filename):
    """Serve a PDF report file."""
    from flask import send_from_directory, abort
    safe = os.path.basename(filename)
    if not safe.endswith(".pdf"):
        abort(400)
    filepath = os.path.join(REPORTS_DIR, safe)
    if not os.path.isfile(filepath):
        abort(404)
    return send_from_directory(REPORTS_DIR, safe, mimetype="application/pdf",
                               as_attachment=True)


@app.route("/api/reports/generate", methods=["POST"])
def api_report_generate():
    """Trigger Rscript to render and optionally email a PDF report.

    POST body:
      days_back       int     (default 7)
      zone_label      str     (default "All Florida")
      severity_filter str     comma-separated or "all"
      event_filter    str     single event name or "all"
      date_from       str     YYYY-MM-DD or ""
      date_to         str     YYYY-MM-DD or ""
      send_email      bool    (default true)
    """
    import subprocess, threading

    with _report_lock:
        if _report_running["value"]:
            return jsonify({"ok": False, "message": "A report is already generating. Please wait."}), 409
        _report_running["value"] = True

    data            = request.get_json(silent=True) or {}
    days_back       = str(int(data.get("days_back", 7)))
    zone_label      = str(data.get("zone_label", "All Florida"))
    severity_filter = str(data.get("severity_filter", "all"))
    event_filter    = str(data.get("event_filter", "all"))
    date_from       = str(data.get("date_from", ""))
    date_to         = str(data.get("date_to", ""))
    send_email_flag = "true" if data.get("send_email", True) else "false"

    try:
        result = subprocess.run(
            [
                "/usr/bin/Rscript", REPORTS_RSCRIPT,
                days_back, zone_label, severity_filter, event_filter,
                date_from, date_to, send_email_flag,
            ],
            capture_output=True, text=True, timeout=180,
            env={**__import__("os").environ, "MONGO_URI": "mongodb://localhost:27017/"},
        )
        output = result.stdout + result.stderr
        _report_running["value"] = False

        if result.returncode != 0:
            return jsonify({"ok": False, "message": output[-400:] or "Rscript failed"}), 500

        # Extract filename from script output
        filename = ""
        for line in output.splitlines():
            if line.startswith("OUTPUT_FILE:"):
                filename = os.path.basename(line.split(":", 1)[1].strip())
                break

        msg = f"PDF generated: {filename}"
        if send_email_flag == "true" and "Email sent" in output:
            msg += " — emailed to " + (data.get("mail_to") or "lawrence.bornace@ufl.edu")
        elif send_email_flag == "true":
            msg += " (email may have failed — check logs)"

        return jsonify({"ok": True, "filename": filename, "message": msg})

    except subprocess.TimeoutExpired:
        _report_running["value"] = False
        return jsonify({"ok": False, "message": "Report generation timed out (>3 min)"}), 504
    except Exception as exc:
        _report_running["value"] = False
        return jsonify({"ok": False, "message": str(exc)}), 500


# -------------------- FEEDBACK ------------------

@app.route("/api/zones")
def api_zones():
    try:
        from pymongo import MongoClient
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
        db = client["weather_rss"]
        zones = list(db["zone_definitions"].find({}, {"zone_id":1,"catch_all":1,"counties":1,"cleanup":1,"_id":0}))
        client.close()
        return jsonify({"zones": zones})
    except Exception as e:
        return jsonify({"zones": [], "error": str(e)})


CONTENT_ROOT = '/home/ufuser/Fpren-main/weather_station/audio/content'
UPLOAD_FOLDERS = {
    'top_of_hour': CONTENT_ROOT + '/top_of_hour',
    'imaging': CONTENT_ROOT + '/imaging',
    'music': CONTENT_ROOT + '/music',
    'educational': CONTENT_ROOT + '/educational',
    'weather_report': CONTENT_ROOT + '/weather_report',
}
ALLOWED_EXT = {'.mp3', '.wav', '.ogg', '.m4a'}

@app.route("/api/upload", methods=["POST"])
def api_upload():
    from werkzeug.utils import secure_filename
    folder = request.form.get("folder","").strip()
    if folder not in UPLOAD_FOLDERS:
        return jsonify({"ok": False, "message": "Invalid folder"}), 400
    if "file" not in request.files:
        return jsonify({"ok": False, "message": "No file"}), 400
    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"ok": False, "message": f"Type {ext} not allowed"}), 400
    name = secure_filename(f.filename)
    dest = os.path.join(UPLOAD_FOLDERS[folder], name)
    os.makedirs(UPLOAD_FOLDERS[folder], exist_ok=True)
    f.save(dest)
    return jsonify({"ok": True, "message": f"Uploaded {name} to {folder}"})

@app.route("/api/upload/list")
def api_upload_list():
    result = {}
    for folder, path in UPLOAD_FOLDERS.items():
        if os.path.isdir(path):
            result[folder] = sorted([
                {"name": f, "size_kb": os.path.getsize(os.path.join(path,f))//1024}
                for f in os.listdir(path)
                if os.path.splitext(f)[1].lower() in ALLOWED_EXT
            ], key=lambda x: x["name"])
        else:
            result[folder] = []
    return jsonify(result)

@app.route("/api/upload/delete", methods=["POST"])
def api_upload_delete():
    from werkzeug.utils import secure_filename
    data = request.get_json(silent=True) or {}
    folder = data.get("folder","").strip()
    filename = data.get("filename","").strip()
    if folder not in UPLOAD_FOLDERS or not filename:
        return jsonify({"ok": False, "message": "Invalid"}), 400
    path = os.path.join(UPLOAD_FOLDERS[folder], secure_filename(filename))
    if not os.path.exists(path):
        return jsonify({"ok": False, "message": "Not found"}), 404
    os.remove(path)
    return jsonify({"ok": True, "message": f"Deleted {filename}"})

@app.route("/api/users", methods=["GET"])
@login_required
def api_users():
    if current_user.role != "admin":
        return jsonify({"ok": False, "message": "Admin only"}), 403
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017/")
    users = list(client["weather_rss"]["users"].find({}, {"password":0}))
    client.close()
    for u in users:
        u["_id"] = str(u["_id"])
        if "created_at" in u:
            u["created_at"] = str(u["created_at"])
    return jsonify({"users": users})

@app.route("/api/users/add", methods=["POST"])
@login_required
def api_users_add():
    if current_user.role != "admin":
        return jsonify({"ok": False, "message": "Admin only"}), 403
    data = request.get_json(silent=True) or {}
    username = data.get("username","").strip()
    password = data.get("password","").strip()
    role     = data.get("role","viewer").strip()
    if not username or not password:
        return jsonify({"ok": False, "message": "Username and password required"}), 400
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    from pymongo import MongoClient
    from datetime import datetime, timezone
    client = MongoClient("mongodb://localhost:27017/")
    col = client["weather_rss"]["users"]
    if col.find_one({"username": username}):
        client.close()
        return jsonify({"ok": False, "message": "Username already exists"}), 400
    col.insert_one({"username": username, "password": hashed, "role": role,
                    "active": True, "created_at": datetime.now(timezone.utc)})
    client.close()
    return jsonify({"ok": True, "message": f"User {username} created"})

@app.route("/api/users/delete", methods=["POST"])
@login_required
def api_users_delete():
    if current_user.role != "admin":
        return jsonify({"ok": False, "message": "Admin only"}), 403
    data = request.get_json(silent=True) or {}
    username = data.get("username","").strip()
    if username == current_user.username:
        return jsonify({"ok": False, "message": "Cannot delete yourself"}), 400
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017/")
    result = client["weather_rss"]["users"].delete_one({"username": username})
    client.close()
    if result.deleted_count:
        return jsonify({"ok": True, "message": f"User {username} deleted"})
    return jsonify({"ok": False, "message": "User not found"}), 404

@app.route("/api/users/password", methods=["POST"])
@login_required
def api_users_password():
    if current_user.role != "admin":
        return jsonify({"ok": False, "message": "Admin only"}), 403
    data = request.get_json(silent=True) or {}
    username = data.get("username","").strip()
    password = data.get("password","").strip()
    if not username or not password:
        return jsonify({"ok": False, "message": "Username and password required"}), 400
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017/")
    result = client["weather_rss"]["users"].update_one(
        {"username": username}, {"$set": {"password": hashed}})
    client.close()
    if result.modified_count:
        return jsonify({"ok": True, "message": f"Password updated for {username}"})
    return jsonify({"ok": False, "message": "User not found"}), 404


# -------------------- USER ASSETS API --------------------
# Assets are stored as an array inside each user's MongoDB document.
# Each asset: { asset_id, asset_name, address, lat, lon, zip, city,
#               nearest_airport_icao, nearest_airport_name, asset_type, notes, created_at }

def _get_user_assets(username):
    """Return (user_doc, assets_list) or (None, []) on error."""
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017/")
    user = client["weather_rss"]["users"].find_one({"username": username}, {"assets": 1})
    client.close()
    if not user:
        return None, []
    return user, user.get("assets", []) or []


@app.route("/api/users/<username>/assets", methods=["GET"])
@login_required
def api_user_assets_list(username):
    """Return a user's assets. Admin or the user themselves."""
    if current_user.role != "admin" and current_user.username != username:
        return jsonify({"ok": False, "message": "Forbidden"}), 403
    user, assets = _get_user_assets(username)
    if user is None:
        return jsonify({"ok": False, "message": "User not found"}), 404
    # Sanitize: stringify any ObjectId-like fields
    for a in assets:
        if "asset_id" in a:
            a["asset_id"] = str(a["asset_id"])
    return jsonify({"ok": True, "assets": assets})


@app.route("/api/users/<username>/assets", methods=["POST"])
@login_required
def api_user_assets_add(username):
    """Add an asset to a user's profile."""
    if current_user.role != "admin" and current_user.username != username:
        return jsonify({"ok": False, "message": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    import uuid
    from datetime import datetime, timezone
    from pymongo import MongoClient

    asset_name = data.get("asset_name", "").strip()
    if not asset_name:
        return jsonify({"ok": False, "message": "asset_name is required"}), 400

    lat = float(data.get("lat", 0))
    lon = float(data.get("lon", 0))

    # Auto-fetch nearby emergency resources if coordinates are available
    nearby = {}
    if lat != 0 and lon != 0:
        try:
            nearby = _fetch_nearby_resources(lat, lon, radius_m=5000)
        except Exception:
            nearby = {}

    new_asset = {
        "asset_id":            str(uuid.uuid4()),
        "asset_name":          asset_name,
        "address":             data.get("address", "").strip(),
        "lat":                 lat,
        "lon":                 lon,
        "zip":                 data.get("zip", "").strip(),
        "city":                data.get("city", "").strip(),
        "nearest_airport_icao": data.get("nearest_airport_icao", "").strip(),
        "nearest_airport_name": data.get("nearest_airport_name", "").strip(),
        "asset_type":          data.get("asset_type", "Facility").strip(),
        "notes":               data.get("notes", "").strip(),
        "created_at":          datetime.now(timezone.utc).isoformat(),
        "nearby_fire_stations":  nearby.get("fire_stations", []),
        "nearby_police":         nearby.get("police", []),
        "nearby_hospitals":      nearby.get("hospitals", []),
        "nearby_supermarkets":   nearby.get("supermarkets", []),
    }
    client = MongoClient("mongodb://localhost:27017/")
    result = client["weather_rss"]["users"].update_one(
        {"username": username},
        {"$push": {"assets": new_asset}}
    )
    client.close()
    if result.matched_count == 0:
        return jsonify({"ok": False, "message": "User not found"}), 404
    n_fire   = len(new_asset["nearby_fire_stations"])
    n_police = len(new_asset["nearby_police"])
    n_hosp   = len(new_asset["nearby_hospitals"])
    n_mkt    = len(new_asset["nearby_supermarkets"])
    return jsonify({
        "ok":      True,
        "message": (f"Asset added ({n_fire} fire station(s), {n_police} police, "
                    f"{n_hosp} hospital(s), {n_mkt} supermarket(s) found nearby)"),
        "asset_id": new_asset["asset_id"],
        "nearby_resources": {
            "fire_stations": new_asset["nearby_fire_stations"],
            "police":        new_asset["nearby_police"],
            "hospitals":     new_asset["nearby_hospitals"],
            "supermarkets":  new_asset["nearby_supermarkets"],
        },
    })


@app.route("/api/users/<username>/assets/<asset_id>", methods=["PUT"])
@login_required
def api_user_assets_update(username, asset_id):
    """Update a specific asset (matched by asset_id)."""
    if current_user.role != "admin" and current_user.username != username:
        return jsonify({"ok": False, "message": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    from pymongo import MongoClient

    allowed = ["asset_name","address","lat","lon","zip","city",
               "nearest_airport_icao","nearest_airport_name","asset_type","notes"]
    set_fields = {}
    for field in allowed:
        if field in data:
            val = data[field]
            if field in ("lat","lon"):
                val = float(val)
            set_fields[f"assets.$.{field}"] = val

    if not set_fields:
        return jsonify({"ok": False, "message": "Nothing to update"}), 400

    client = MongoClient("mongodb://localhost:27017/")
    result = client["weather_rss"]["users"].update_one(
        {"username": username, "assets.asset_id": asset_id},
        {"$set": set_fields}
    )
    client.close()
    if result.matched_count == 0:
        return jsonify({"ok": False, "message": "Asset not found"}), 404
    return jsonify({"ok": True, "message": "Asset updated"})


@app.route("/api/users/<username>/assets/<asset_id>", methods=["DELETE"])
@login_required
def api_user_assets_delete(username, asset_id):
    """Remove an asset from a user's profile."""
    if current_user.role != "admin" and current_user.username != username:
        return jsonify({"ok": False, "message": "Forbidden"}), 403
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017/")
    result = client["weather_rss"]["users"].update_one(
        {"username": username},
        {"$pull": {"assets": {"asset_id": asset_id}}}
    )
    client.close()
    if result.matched_count == 0:
        return jsonify({"ok": False, "message": "User not found"}), 404
    return jsonify({"ok": True, "message": "Asset removed"})


# -------------------- CITY / AIRPORT LOOKUP BY ZIP --------------------

# FL airport reference: ICAO, name, city, lat, lon
_FL_AIRPORTS = [
    {"icao": "KJAX", "name": "Jacksonville Intl",           "city": "Jacksonville",    "lat": 30.49, "lon": -81.69},
    {"icao": "KTLH", "name": "Tallahassee Intl",            "city": "Tallahassee",     "lat": 30.40, "lon": -84.35},
    {"icao": "KGNV", "name": "Gainesville Regional",        "city": "Gainesville",     "lat": 29.69, "lon": -82.27},
    {"icao": "KOCF", "name": "Ocala Intl",                  "city": "Ocala",           "lat": 29.17, "lon": -82.22},
    {"icao": "KMCO", "name": "Orlando Intl",                 "city": "Orlando",        "lat": 28.43, "lon": -81.31},
    {"icao": "KDAB", "name": "Daytona Beach Intl",          "city": "Daytona Beach",   "lat": 29.18, "lon": -81.06},
    {"icao": "KTPA", "name": "Tampa Intl",                  "city": "Tampa",           "lat": 27.98, "lon": -82.53},
    {"icao": "KSPG", "name": "St. Pete-Clearwater Intl",   "city": "St. Petersburg",  "lat": 27.92, "lon": -82.69},
    {"icao": "KSRQ", "name": "Sarasota-Bradenton Intl",    "city": "Sarasota",        "lat": 27.40, "lon": -82.55},
    {"icao": "KRSW", "name": "Southwest FL Intl",           "city": "Fort Myers",      "lat": 26.54, "lon": -81.76},
    {"icao": "KMIA", "name": "Miami Intl",                  "city": "Miami",           "lat": 25.80, "lon": -80.29},
    {"icao": "KFLL", "name": "Fort Lauderdale-Hollywood Intl","city":"Fort Lauderdale","lat": 26.07, "lon": -80.15},
    {"icao": "KPBI", "name": "Palm Beach Intl",             "city": "West Palm Beach", "lat": 26.68, "lon": -80.10},
    {"icao": "KEYW", "name": "Key West Intl",               "city": "Key West",        "lat": 24.56, "lon": -81.76},
    {"icao": "KPNS", "name": "Pensacola Intl",              "city": "Pensacola",       "lat": 30.47, "lon": -87.19},
    {"icao": "KECP", "name": "Northwest FL Beaches Intl",  "city": "Panama City",     "lat": 30.36, "lon": -85.80},
]

# Simple FL county centroids for ZIP range resolution
_FL_COUNTY_CENTROIDS = {
    "Alachua": (29.67, -82.33), "Baker": (30.33, -82.29), "Bay": (30.21, -85.68),
    "Bradford": (29.94, -82.17), "Brevard": (28.23, -80.73), "Broward": (26.15, -80.45),
    "Charlotte": (26.97, -81.95), "Citrus": (28.84, -82.50), "Clay": (29.98, -81.80),
    "Collier": (26.11, -81.41), "Columbia": (30.22, -82.63), "Miami-Dade": (25.55, -80.63),
    "Duval": (30.33, -81.65), "Escambia": (30.55, -87.34), "Flagler": (29.47, -81.27),
    "Hernando": (28.55, -82.47), "Highlands": (27.35, -81.34), "Hillsborough": (27.90, -82.35),
    "Indian River": (27.74, -80.61), "Jackson": (30.83, -85.22), "Lake": (28.76, -81.72),
    "Lee": (26.56, -81.77), "Leon": (30.46, -84.28), "Manatee": (27.48, -82.34),
    "Marion": (29.23, -82.13), "Martin": (27.07, -80.42), "Monroe": (24.93, -81.08),
    "Nassau": (30.61, -81.77), "Okaloosa": (30.65, -86.52), "Okeechobee": (27.39, -80.90),
    "Orange": (28.48, -81.31), "Osceola": (28.06, -81.15), "Palm Beach": (26.65, -80.28),
    "Pasco": (28.31, -82.43), "Pinellas": (27.88, -82.72), "Polk": (27.93, -81.68),
    "Putnam": (29.63, -81.74), "Saint Johns": (29.90, -81.44), "Saint Lucie": (27.38, -80.44),
    "Santa Rosa": (30.72, -87.01), "Sarasota": (27.18, -82.37), "Seminole": (28.72, -81.21),
    "Sumter": (28.70, -82.07), "Suwannee": (30.19, -83.00), "Taylor": (30.06, -83.60),
    "Volusia": (29.03, -81.07), "Walton": (30.64, -86.13), "Washington": (30.61, -85.67),
}

# Coarse FL ZIP-to-county range map (county, zip_min, zip_max)
_FL_ZIP_RANGES = [
    ("Escambia",30999,32600),("Santa Rosa",32507,32583),("Okaloosa",32531,32579),
    ("Walton",32413,32462),("Washington",32427,32446),("Bay",32401,32413),
    ("Jackson",32420,32445),("Calhoun",32421,32424),("Gulf",32456,32465),
    ("Franklin",32320,32329),("Gadsden",32303,32333),("Liberty",32314,32321),
    ("Leon",32301,32317),("Wakulla",32327,32327),("Jefferson",32336,32344),
    ("Madison",32059,32061),("Taylor",32347,32360),("Hamilton",32052,32052),
    ("Suwannee",32008,32060),("Lafayette",32066,32066),("Columbia",32024,32026),
    ("Dixie",32628,32628),("Gilchrist",32619,32693),("Alachua",32601,32699),
    ("Levy",32621,32621),("Putnam",32112,32187),("Marion",32112,34488),
    ("Citrus",34428,34465),("Hernando",34601,34614),("Baker",32040,32040),
    ("Nassau",32011,32034),("Duval",32099,32277),("Clay",32003,32099),
    ("Saint Johns",32033,32095),("Flagler",32110,32136),("Volusia",32114,32198),
    ("Sumter",33585,34785),("Lake",32702,34797),("Pasco",33523,34691),
    ("Pinellas",33701,34695),("Hillsborough",33510,34289),("Polk",33801,34292),
    ("Osceola",34739,34773),("Brevard",32780,32955),("Orange",32703,34761),
    ("Seminole",32701,32773),("Manatee",34201,34292),("Sarasota",34201,34293),
    ("Charlotte",33946,33982),("DeSoto",34266,34269),("Highlands",33825,33876),
    ("Hardee",33834,33843),("Okeechobee",34972,34974),("Indian River",32948,32968),
    ("Saint Lucie",34945,34990),("Martin",34994,34997),("Palm Beach",33401,33498),
    ("Broward",33004,33388),("Miami-Dade",33010,33299),("Collier",34101,34145),
    ("Lee",33901,34107),("Monroe",33001,33999),
]

def _zip_to_county(zip_str):
    """Map a 5-digit FL ZIP to a county name, or None."""
    try:
        z = int(zip_str)
    except (ValueError, TypeError):
        return None
    for county, lo, hi in _FL_ZIP_RANGES:
        if lo <= z <= hi:
            return county
    return None


def _nearest_airport(lat, lon):
    """Return the airport in _FL_AIRPORTS closest to (lat, lon)."""
    import math
    best, best_dist = _FL_AIRPORTS[0], float("inf")
    for apt in _FL_AIRPORTS:
        d = math.sqrt((apt["lat"] - lat) ** 2 + (apt["lon"] - lon) ** 2)
        if d < best_dist:
            best, best_dist = apt, d
    return best


def _fetch_nearby_resources(lat: float, lon: float, radius_m: int = 5000) -> dict:
    """
    Find nearby emergency resources using multiple data sources:
      1. OpenStreetMap Overpass — spatial discovery (what is near the coordinates)
      2. Nominatim reverse geocode — fills in street addresses when OSM tags are sparse
      3. CMS NPI Registry — authoritative US hospital/clinic names, addresses, and phone numbers
    Returns dict with keys: fire_stations, police, hospitals, supermarkets.
    Never raises; returns empty lists on any error.
    """
    import math
    import requests
    import time

    _NOMINATIM_UA = "FPREN/1.0 (fpren@ufl.edu)"

    # ── helpers ──────────────────────────────────────────────────────────────

    def _dist_km(elat, elon):
        dlat = (elat - lat) * math.pi / 180
        dlon = (elon - lon) * math.pi / 180
        a = (math.sin(dlat/2)**2
             + math.cos(lat*math.pi/180) * math.cos(elat*math.pi/180)
             * math.sin(dlon/2)**2)
        return round(6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)), 2)

    def _extract_phone(tags):
        for key in ("phone", "contact:phone", "contact:mobile",
                    "phone:en", "emergency:phone", "telephone",
                    "phone_1", "mobile"):
            v = tags.get(key, "").strip()
            if v:
                return v
        return ""

    def _extract_address(tags):
        full = tags.get("addr:full", "").strip()
        if full:
            return full
        parts = [
            tags.get("addr:housenumber", "").strip(),
            tags.get("addr:street", "").strip(),
            tags.get("addr:city", "").strip(),
            tags.get("addr:state", "").strip(),
            tags.get("addr:postcode", "").strip(),
        ]
        built = ", ".join(p for p in parts if p)
        if not built:
            city  = tags.get("addr:city", "").strip()
            state = tags.get("addr:state", "").strip()
            built = ", ".join(p for p in [city, state] if p)
        return built

    def _nominatim_reverse(rlat, rlon):
        """Reverse geocode a coordinate to a street address via Nominatim."""
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": rlat, "lon": rlon, "format": "jsonv2"},
                headers={"User-Agent": _NOMINATIM_UA},
                timeout=8,
            )
            if r.status_code != 200:
                return ""
            addr = r.json().get("address", {})
            num    = addr.get("house_number", "").strip()
            street = addr.get("road", "").strip()
            city   = (addr.get("city") or addr.get("town") or addr.get("village", "")).strip()
            state  = addr.get("state", "").strip()
            pcode  = addr.get("postcode", "").strip()
            street_full = f"{num} {street}".strip() if num else street
            return ", ".join(p for p in [street_full, city, state, pcode] if p)
        except Exception:
            return ""

    # ── Step 1: OSM Overpass — spatial discovery ───────────────────────────

    police_r = max(radius_m, 10000)
    query = f"""
[out:json][timeout:25];
(
  node["amenity"="fire_station"](around:{radius_m},{lat},{lon});
  way["amenity"="fire_station"](around:{radius_m},{lat},{lon});
  node["amenity"="police"](around:{police_r},{lat},{lon});
  way["amenity"="police"](around:{police_r},{lat},{lon});
  node["amenity"="hospital"](around:{radius_m},{lat},{lon});
  way["amenity"="hospital"](around:{radius_m},{lat},{lon});
  node["amenity"="clinic"](around:{radius_m},{lat},{lon});
  way["amenity"="clinic"](around:{radius_m},{lat},{lon});
  node["amenity"="doctors"](around:{radius_m},{lat},{lon});
  node["shop"="supermarket"](around:{radius_m},{lat},{lon});
  way["shop"="supermarket"](around:{radius_m},{lat},{lon});
  node["shop"="grocery"](around:{radius_m},{lat},{lon});
);
out center tags;
"""
    result = {"fire_stations": [], "police": [], "hospitals": [], "supermarkets": []}
    _overpass_mirrors = [
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass-api.de/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
    ]
    elements = []
    for _mirror in _overpass_mirrors:
        try:
            resp = requests.post(
                _mirror,
                data={"data": query},
                timeout=30,
                headers={"User-Agent": _NOMINATIM_UA},
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            break
        except Exception:
            continue

    seen: dict[str, set] = {k: set() for k in result}
    for el in elements:
        tags = el.get("tags", {})
        elat = el.get("lat") or (el.get("center") or {}).get("lat")
        elon = el.get("lon") or (el.get("center") or {}).get("lon")
        if elat is None or elon is None:
            continue
        amenity = tags.get("amenity", "")
        shop    = tags.get("shop", "")
        if amenity == "fire_station":
            cat = "fire_stations"
        elif amenity == "police":
            cat = "police"
        elif amenity in ("hospital", "clinic", "doctors"):
            cat = "hospitals"
        elif shop in ("supermarket", "grocery"):
            cat = "supermarkets"
        else:
            continue
        name = (tags.get("name") or tags.get("operator")
                or tags.get("brand") or "Unnamed").strip()
        key = name.lower()
        if key in seen[cat]:
            continue
        seen[cat].add(key)
        result[cat].append({
            "name":    name,
            "address": _extract_address(tags),
            "phone":   _extract_phone(tags),
            "lat":     elat,
            "lon":     elon,
            "dist_km": _dist_km(elat, elon),
            "subtype": amenity or shop,
            "source":  "OSM",
        })

    # ── Step 2: Nominatim reverse geocode — fill missing addresses ─────────
    # Rate limit: 1 request/second per Nominatim usage policy.
    # Only call for items where OSM tags gave us no address at all.
    # Nominatim rate limit: 1 req/sec. Cap per category to keep total latency
    # under ~8 s (2 fire + 2 police + 1 origin + 2 hospital fallback = 7 calls max).

    _NOMINATIM_PER_CAT = 2   # geocode at most the 2 closest items per category
    _last_nominatim = 0.0

    def _has_street_address(addr):
        """True only if addr looks like a real street address (has digits and a comma)."""
        return bool(addr) and ("," in addr) and any(c.isdigit() for c in addr)

    def _rate_limited_geocode(item):
        """Apply Nominatim reverse geocode to item; enforces 1-req/sec rate limit."""
        nonlocal _last_nominatim
        now = time.monotonic()
        gap = now - _last_nominatim
        if gap < 1.1:
            time.sleep(1.1 - gap)
        addr = _nominatim_reverse(item["lat"], item["lon"])
        _last_nominatim = time.monotonic()
        return addr

    # Sort all categories by distance before enrichment so the budget is spent
    # on the closest items first.
    for k in result:
        result[k].sort(key=lambda x: x["dist_km"])

    for cat in ("fire_stations", "police"):  # hospitals handled separately by NPI
        budget = _NOMINATIM_PER_CAT
        for item in result[cat]:
            if _has_street_address(item["address"]) or budget <= 0:
                continue
            addr = _rate_limited_geocode(item)
            if addr:
                item["address"] = addr
                item["source"] = "OSM+Nominatim"
            budget -= 1

    # ── Step 3: CMS NPI Registry — hospital address + phone enrichment ─────
    # The NPI registry (npiregistry.cms.hhs.gov) is a free US government API
    # containing all registered healthcare providers with verified addresses
    # and phone numbers. Query by city to enrich OSM hospital results.

    def _get_npi_data(city_name, state_abbr="FL"):
        """
        Fetch organization-type NPI records for hospitals/clinics in the given
        city. Returns dict keyed by lowercased name for fuzzy matching.
        """
        npi_index = {}
        for taxonomy in ("hospital", "clinic", "urgent care"):
            try:
                r = requests.get(
                    "https://npiregistry.cms.hhs.gov/api/",
                    params={
                        "version":              "2.1",
                        "enumeration_type":     "NPI-2",  # organizations only
                        "city":                 city_name,
                        "state":                state_abbr,
                        "taxonomy_description": taxonomy,
                        "limit":                50,
                    },
                    timeout=10,
                )
                if r.status_code != 200:
                    continue
                for rec in r.json().get("results", []):
                    org_name = rec["basic"].get("organization_name", "").strip()
                    if not org_name:
                        continue
                    loc = next(
                        (a for a in rec.get("addresses", [])
                         if a.get("address_purpose") == "LOCATION"),
                        {}
                    )
                    phone = loc.get("telephone_number", "").strip()
                    num   = loc.get("address_1", "").strip()
                    city  = loc.get("city", "").strip().title()
                    st    = loc.get("state", "").strip()
                    pcode = loc.get("postal_code", "")[:5]
                    addr  = ", ".join(p for p in [num, city, st, pcode] if p)
                    key   = org_name.lower()
                    # Keep entry with more data if duplicate name
                    if key not in npi_index or (not npi_index[key]["phone"] and phone):
                        npi_index[key] = {"name": org_name, "phone": phone, "address": addr}
            except Exception:
                continue
        return npi_index

    def _name_tokens(s):
        """Lower-cased significant words (len >= 4) for fuzzy matching."""
        return {w for w in s.lower().split() if len(w) >= 4}

    if result["hospitals"]:
        # One Nominatim call to get the city name for the NPI query
        now = time.monotonic()
        gap = now - _last_nominatim
        if gap < 1.1:
            time.sleep(1.1 - gap)
        origin_addr = _nominatim_reverse(lat, lon)
        _last_nominatim = time.monotonic()

        # Parse city out of "123 Main St, Gainesville, Florida, 32601"
        city_name = "Unknown"
        if origin_addr:
            parts = [p.strip() for p in origin_addr.split(",")]
            # city is usually the second part (after street)
            if len(parts) >= 2:
                city_name = parts[1].strip()

        npi_index = _get_npi_data(city_name) if city_name != "Unknown" else {}

        for item in result["hospitals"]:
            name_lower = item["name"].lower()
            # 1. Exact name match
            match = npi_index.get(name_lower)
            if not match:
                # 2. Substring or significant-token overlap
                item_tokens = _name_tokens(item["name"])
                for npi_key, npi_val in npi_index.items():
                    npi_tokens = _name_tokens(npi_key)
                    overlap = item_tokens & npi_tokens
                    if overlap and (len(overlap) / max(len(item_tokens), 1)) >= 0.5:
                        match = npi_val
                        break
            if match:
                if match.get("phone") and not item["phone"]:
                    item["phone"] = match["phone"]
                if match.get("address") and not item["address"]:
                    item["address"] = match["address"]
                item["source"] = "OSM+NPI"

        # Nominatim fallback for hospitals still missing a street address after NPI
        hosp_budget = _NOMINATIM_PER_CAT
        for item in result["hospitals"]:
            if _has_street_address(item["address"]) or hosp_budget <= 0:
                continue
            addr = _rate_limited_geocode(item)
            if addr:
                item["address"] = addr
                src = item.get("source", "OSM")
                item["source"] = src if "Nominatim" in src else src + "+Nominatim"
            hosp_budget -= 1

    # ── Sort by distance and return ───────────────────────────────────────
    for k in result:
        result[k].sort(key=lambda x: x["dist_km"])

    return result


@app.route("/api/lookup/nearby-resources")
def api_lookup_nearby_resources():
    """
    GET /api/lookup/nearby-resources?lat=29.65&lon=-82.33&radius_m=5000
    Returns nearest fire stations, police, hospitals, and supermarkets.
    Data sources: OSM Overpass (spatial), Nominatim (address enrichment), CMS NPI (hospital phones).
    """
    try:
        lat      = float(request.args["lat"])
        lon      = float(request.args["lon"])
        radius_m = int(request.args.get("radius_m", 5000))
    except (KeyError, ValueError):
        return jsonify({"ok": False, "message": "lat and lon are required numeric params"}), 400
    try:
        resources = _fetch_nearby_resources(lat, lon, radius_m)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500
    return jsonify({"ok": True, "radius_m": radius_m, **resources})


@app.route("/api/lookup/city-by-zip")
def api_lookup_city_by_zip():
    """
    GET /api/lookup/city-by-zip?zip=32601
    Returns: { county, city, lat, lon, nearest_airport_icao, nearest_airport_name }
    """
    zip_str = request.args.get("zip", "").strip()
    if not zip_str or not zip_str.isdigit() or len(zip_str) != 5:
        return jsonify({"ok": False, "message": "Valid 5-digit ZIP required"}), 400

    county = _zip_to_county(zip_str)
    if county is None:
        return jsonify({"ok": False, "message": "ZIP not found in FL range"}), 404

    lat, lon = _FL_COUNTY_CENTROIDS.get(county, (29.65, -82.33))
    apt = _nearest_airport(lat, lon)

    return jsonify({
        "ok":                   True,
        "zip":                  zip_str,
        "county":               county,
        "city":                 apt["city"],
        "lat":                  lat,
        "lon":                  lon,
        "nearest_airport_icao": apt["icao"],
        "nearest_airport_name": apt["name"],
    })


@app.route("/api/lookup/geocode")
def api_lookup_geocode():
    """
    GET /api/lookup/geocode?address=1600+SW+23rd+Dr&zip=32608
    Geocodes the address via Nominatim (OpenStreetMap), falls back to ZIP
    centroid if Nominatim fails or returns no results.
    Returns: { lat, lon, county, city, nearest_airport_icao, nearest_airport_name,
               source: "nominatim"|"zip_centroid" }
    """
    address = request.args.get("address", "").strip()
    zip_str = request.args.get("zip", "").strip()

    if not address and (not zip_str or not zip_str.isdigit() or len(zip_str) != 5):
        return jsonify({"ok": False, "message": "address or valid ZIP required"}), 400

    lat = lon = None
    source = "zip_centroid"

    # Try Nominatim geocoding first
    if address:
        query = address
        if zip_str:
            query = f"{address}, {zip_str}, Florida, USA"
        else:
            query = f"{address}, Florida, USA"
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
                timeout=10,
                headers={"User-Agent": "FPREN/1.0 (florida-public-radio-emergency-network)"},
            )
            resp.raise_for_status()
            results = resp.json()
            if results:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                source = "nominatim"
        except Exception:
            pass  # fall through to ZIP centroid

    # Fall back to ZIP centroid
    if lat is None or lon is None:
        if zip_str and zip_str.isdigit() and len(zip_str) == 5:
            county = _zip_to_county(zip_str)
            if county:
                lat, lon = _FL_COUNTY_CENTROIDS.get(county, (29.65, -82.33))
            else:
                return jsonify({"ok": False, "message": "Could not geocode address and ZIP not in FL"}), 404
        else:
            return jsonify({"ok": False, "message": "Geocoding failed — provide a valid FL ZIP as fallback"}), 404

    # Determine county from coords via ZIP fallback or reverse county lookup
    county = None
    if zip_str and zip_str.isdigit():
        county = _zip_to_county(zip_str)
    if county is None:
        # Best-effort: find nearest county centroid
        best_c, best_d = None, float("inf")
        for c, (clat, clon) in _FL_COUNTY_CENTROIDS.items():
            d = (clat - lat) ** 2 + (clon - lon) ** 2
            if d < best_d:
                best_c, best_d = c, d
        county = best_c or "Alachua"

    apt = _nearest_airport(lat, lon)

    return jsonify({
        "ok":                   True,
        "lat":                  round(lat, 6),
        "lon":                  round(lon, 6),
        "county":               county,
        "city":                 apt["city"],
        "nearest_airport_icao": apt["icao"],
        "nearest_airport_name": apt["name"],
        "source":               source,
    })


# -------------------- BCP REPORT GENERATION --------------------

_bcp_lock    = __import__("threading").Lock()
_bcp_running = {"value": False}
BCP_RSCRIPT  = "/home/ufuser/Fpren-main/reports/business_continuity_report.Rmd"


@app.route("/api/reports/generate-bcp", methods=["POST"])
@login_required
def api_report_generate_bcp():
    """
    Render a Business Continuity Plan PDF for a specific user asset.
    POST body: { username, asset_id }   (admin required, or own username)
    """
    import subprocess

    data     = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    asset_id = data.get("asset_id", "").strip()

    if not username or not asset_id:
        return jsonify({"ok": False, "message": "username and asset_id required"}), 400

    if current_user.role != "admin" and current_user.username != username:
        return jsonify({"ok": False, "message": "Forbidden"}), 403

    with _bcp_lock:
        if _bcp_running["value"]:
            return jsonify({"ok": False, "message": "A BCP is already generating. Please wait."}), 409
        _bcp_running["value"] = True

    # Fetch the specific asset
    _, assets = _get_user_assets(username)
    asset = next((a for a in assets if str(a.get("asset_id")) == asset_id), None)
    if asset is None:
        _bcp_running["value"] = False
        return jsonify({"ok": False, "message": "Asset not found"}), 404

    import os
    from datetime import datetime, timezone

    stamp     = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    safe_name = "".join(c if c.isalnum() else "_" for c in asset.get("asset_name", "asset"))
    out_file  = os.path.join(REPORTS_DIR, f"bcp_{username}_{safe_name}_{stamp}.pdf")

    # Build an inline R script that renders the Rmd with the asset params
    lat = float(asset.get("lat", 29.65))
    lon = float(asset.get("lon", -82.33))
    r_script = f"""
suppressPackageStartupMessages({{library(rmarkdown); library(withr)}})
withr::with_dir(tempdir(), rmarkdown::render(
  input             = {repr(BCP_RSCRIPT)},
  output_file       = {repr(out_file)},
  intermediates_dir = tempdir(),
  params = list(
    username             = {repr(username)},
    asset_name           = {repr(asset.get("asset_name",""))},
    address              = {repr(asset.get("address",""))},
    lat                  = {lat},
    lon                  = {lon},
    zip                  = {repr(asset.get("zip",""))},
    city                 = {repr(asset.get("city",""))},
    nearest_airport_icao = {repr(asset.get("nearest_airport_icao","KGNV"))},
    nearest_airport_name = {repr(asset.get("nearest_airport_name","Gainesville Regional"))},
    asset_type           = {repr(asset.get("asset_type","Facility"))},
    notes                = {repr(asset.get("notes",""))},
    mongo_uri            = "mongodb://localhost:27017/",
    days_back            = 30
  ),
  quiet = TRUE
))
cat("BCP_OUTPUT_FILE:", {repr(out_file)}, "\\n")
"""

    try:
        result = subprocess.run(
            ["/usr/bin/Rscript", "-e", r_script],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "MONGO_URI": "mongodb://localhost:27017/"},
        )
        _bcp_running["value"] = False
        if result.returncode != 0:
            return jsonify({"ok": False, "message": (result.stderr or result.stdout)[-500:]}), 500
        filename = os.path.basename(out_file)
        return jsonify({"ok": True, "filename": filename,
                        "message": f"BCP generated: {filename}"})
    except subprocess.TimeoutExpired:
        _bcp_running["value"] = False
        return jsonify({"ok": False, "message": "BCP generation timed out (>5 min)"}), 504
    except Exception as exc:
        _bcp_running["value"] = False
        return jsonify({"ok": False, "message": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# CENSUS DATA API
# ─────────────────────────────────────────────────────────────────────────────

def _census_analyzer():
    """Lazy-import census_ai_analyzer (avoids path issues at startup)."""
    import sys, os
    fpren_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    if fpren_root not in sys.path:
        sys.path.insert(0, fpren_root)
    import importlib
    return importlib.import_module("weather_rss.census_ai_analyzer")


@app.route("/api/census/counties")
def api_census_counties():
    """Return all FL counties with census data, sorted by vulnerability score."""
    try:
        ca = _census_analyzer()
        docs = ca.get_all_counties_census()
        # Strip MongoDB _id, truncate raw int fields for JSON efficiency
        clean = []
        for d in docs:
            d.pop("_id", None)
            clean.append({
                "county":                 d.get("county"),
                "fips_county":            d.get("fips_county"),
                "year":                   d.get("year"),
                "population_total":       d.get("population_total"),
                "pct_65plus":             d.get("pct_65plus"),
                "pct_under18":            d.get("pct_under18"),
                "pct_poverty":            d.get("pct_poverty"),
                "pct_limited_english":    d.get("pct_limited_english"),
                "pct_disability":         d.get("pct_disability"),
                "median_household_income":d.get("median_household_income"),
                "vulnerability_score":    d.get("vulnerability_score"),
                "vulnerability_label":    ca.vulnerability_label(d.get("vulnerability_score", 0)),
                "fetched_at":             d.get("fetched_at"),
            })
        return jsonify({"ok": True, "count": len(clean), "counties": clean})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e), "counties": []}), 500


@app.route("/api/census/county/<county_name>")
def api_census_county(county_name):
    """Return full census record for a single county."""
    try:
        ca = _census_analyzer()
        doc = ca.get_county_census(county_name)
        if doc is None:
            return jsonify({"ok": False, "message": f"No census data for {county_name}"}), 404
        doc.pop("_id", None)
        doc["vulnerability_label"] = ca.vulnerability_label(doc.get("vulnerability_score", 0))
        return jsonify({"ok": True, "data": doc})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/census/analysis/<county_name>")
def api_census_analysis(county_name):
    """
    Return LiteLLM-generated vulnerability analysis for a county.
    Query params:
      ?mode=vulnerability|impact|bcp   (default: vulnerability)
      ?asset=<asset name>              (for BCP mode)
    """
    mode       = request.args.get("mode", "vulnerability")
    asset_name = request.args.get("asset", "")
    try:
        ca    = _census_analyzer()
        census = ca.get_county_census(county_name)
        if census is None:
            return jsonify({"ok": False, "message": f"No census data for {county_name}"}), 404

        if mode == "impact":
            alerts = ca.get_active_alerts_for_county(county_name)
            text   = ca.analyze_alert_impact(county_name, alerts, census)
            return jsonify({"ok": True, "mode": mode, "county": county_name,
                            "alert_count": len(alerts), "analysis": text})
        elif mode == "bcp":
            text = ca.analyze_bcp_demographics(county_name, asset_name, census)
            return jsonify({"ok": True, "mode": mode, "county": county_name,
                            "asset": asset_name, "analysis": text})
        else:
            text = ca.analyze_county_vulnerability(county_name, census)
            return jsonify({"ok": True, "mode": mode, "county": county_name,
                            "vulnerability_score": census.get("vulnerability_score"),
                            "vulnerability_label": ca.vulnerability_label(census.get("vulnerability_score", 0)),
                            "analysis": text})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/census/impact/<alert_id>")
def api_census_alert_impact(alert_id):
    """Return census-enriched alert with AI population impact assessment."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        from bson import ObjectId
        try:
            oid = ObjectId(alert_id)
            alert = client["weather_rss"]["nws_alerts"].find_one({"_id": oid})
        except Exception:
            alert = client["weather_rss"]["nws_alerts"].find_one({"alert_id": alert_id})
        client.close()
        if not alert:
            return jsonify({"ok": False, "message": "Alert not found"}), 404
        alert.pop("_id", None)
        ca = _census_analyzer()
        enriched = ca.enrich_alert_with_census(alert)
        return jsonify({"ok": True, "alert": enriched})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/census/refresh", methods=["POST"])
@login_required
def api_census_refresh():
    """Trigger a fresh census data pull from the Census Bureau API. Admin only."""
    if current_user.role != "admin":
        return jsonify({"ok": False, "message": "Admin only"}), 403
    import subprocess
    fetcher = os.path.join(os.path.dirname(__file__), "..", "fl_census_fetcher.py")
    fetcher = os.path.normpath(fetcher)
    try:
        result = subprocess.run(
            ["python3", fetcher],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(fetcher),
            env={**os.environ, "MONGO_URI": MONGO_URI},
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return jsonify({"ok": True, "message": output.strip()[-300:]})
        return jsonify({"ok": False, "message": output.strip()[-400:]}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "message": "Census fetch timed out (>60s)"}), 504
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# EVACUATION DATA API
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/evacuation/zones")
def api_evacuation_zones():
    """Return all FL hurricane evacuation zones, optionally filtered by county."""
    county = request.args.get("county", "").strip()
    try:
        col = db["fl_evacuation_zones"]
        query = {}
        if county:
            query["county"] = {"$regex": f"^{county}$", "$options": "i"}
        docs = list(col.find(query, {"_id": 0}).sort([("county", 1), ("zone_order", 1)]))
        return jsonify({"ok": True, "count": len(docs), "zones": docs})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e), "zones": []}), 500


@app.route("/api/evacuation/routes")
def api_evacuation_routes():
    """Return FL evacuation routes, optionally filtered by county or region."""
    county = request.args.get("county", "").strip()
    region = request.args.get("region", "").strip()
    try:
        col = db["fl_evacuation_routes"]
        query = {}
        if county:
            query["county"] = {"$regex": f"^{county}$", "$options": "i"}
        if region:
            query["region"] = {"$regex": region, "$options": "i"}
        docs = list(col.find(query, {"_id": 0}).sort([("county", 1), ("route_type", 1)]))
        return jsonify({"ok": True, "count": len(docs), "routes": docs})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e), "routes": []}), 500


@app.route("/api/evacuation/county/<county_name>")
def api_evacuation_county(county_name):
    """Return zones and routes for a single county plus any active traffic on those roads."""
    try:
        zones_col  = db["fl_evacuation_zones"]
        routes_col = db["fl_evacuation_routes"]
        traffic_col = db["fl_traffic"]

        zones  = list(zones_col.find(
            {"county": {"$regex": f"^{county_name}$", "$options": "i"}},
            {"_id": 0}
        ).sort("zone_order", 1))

        routes = list(routes_col.find(
            {"county": {"$regex": f"^{county_name}$", "$options": "i"}},
            {"_id": 0}
        ).sort("route_type", 1))

        # Pull traffic incidents that match any route road name
        road_names = list({r["road"] for r in routes if r.get("road")})
        traffic = []
        if road_names:
            road_regex = "|".join(road_names[:10])
            traffic = list(traffic_col.find(
                {"$or": [
                    {"county": {"$regex": f"^{county_name}$", "$options": "i"}},
                    {"road":   {"$regex": road_regex, "$options": "i"}},
                ]},
                {"_id": 0}
            ).limit(20))

        # Highest-risk zone for this county
        highest_zone = zones[0]["zone"] if zones else None

        return jsonify({
            "ok":          True,
            "county":      county_name,
            "zones":       zones,
            "routes":      routes,
            "traffic":     traffic,
            "highest_zone": highest_zone,
        })
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/evacuation/refresh", methods=["POST"])
@login_required
def api_evacuation_refresh():
    """Trigger a fresh evacuation data pull. Admin only."""
    if current_user.role != "admin":
        return jsonify({"ok": False, "message": "Admin only"}), 403
    import subprocess
    fetcher = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "fl_evacuation_fetcher.py")
    )
    try:
        result = subprocess.run(
            ["python3", fetcher],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(fetcher),
            env={**os.environ, "MONGO_URI": MONGO_URI},
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return jsonify({"ok": True, "message": output.strip()[-300:]})
        return jsonify({"ok": False, "message": output.strip()[-400:]}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "message": "Evacuation fetch timed out (>60s)"}), 504
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Waze for Cities API
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/waze/alerts")
def api_waze_alerts():
    """Return recent Waze alerts, optionally filtered by type or bounding box."""
    try:
        query = {}
        alert_type = request.args.get("type", "").strip().upper()
        city        = request.args.get("city", "").strip()
        hours       = int(request.args.get("hours", 2))
        limit       = min(int(request.args.get("limit", 500)), 2000)

        if alert_type:
            query["type"] = alert_type
        if city:
            query["city"] = {"$regex": city, "$options": "i"}
        if hours > 0:
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            query["fetched_at"] = {"$gte": cutoff}

        docs = list(db["waze_alerts"].find(query, {"_id": 0}).limit(limit))
        return jsonify({"ok": True, "count": len(docs), "alerts": docs})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/waze/jams")
def api_waze_jams():
    """Return recent Waze jams, optionally filtered by level or city."""
    try:
        query = {}
        city      = request.args.get("city", "").strip()
        min_level = request.args.get("min_level", "")
        hours     = int(request.args.get("hours", 2))
        limit     = min(int(request.args.get("limit", 300)), 2000)

        if city:
            query["city"] = {"$regex": city, "$options": "i"}
        if min_level.isdigit():
            query["level"] = {"$gte": int(min_level)}
        if hours > 0:
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            query["fetched_at"] = {"$gte": cutoff}

        docs = list(db["waze_jams"].find(query, {"_id": 0}).limit(limit))
        return jsonify({"ok": True, "count": len(docs), "jams": docs})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/waze/nearby")
def api_waze_nearby():
    """
    Return Waze alerts and jams within `radius_m` metres of a lat/lon point.
    Query params: lat, lon, radius_m (default 10000), hours (default 2)
    Uses MongoDB $nearSphere on the 2dsphere `location` index.
    """
    try:
        lat      = float(request.args["lat"])
        lon      = float(request.args["lon"])
        radius_m = float(request.args.get("radius_m", 10000))
        hours    = int(request.args.get("hours", 2))

        from datetime import datetime, timezone, timedelta
        time_query = {}
        if hours > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            time_query["fetched_at"] = {"$gte": cutoff}

        geo_query = {
            "location": {
                "$nearSphere": {
                    "$geometry":    {"type": "Point", "coordinates": [lon, lat]},
                    "$maxDistance": radius_m,
                }
            }
        }

        alert_query = {**geo_query, **time_query}
        jam_query   = {**geo_query, **time_query}

        alerts = list(db["waze_alerts"].find(alert_query, {"_id": 0}).limit(200))
        jams   = list(db["waze_jams"].find(jam_query,   {"_id": 0}).limit(100))

        return jsonify({
            "ok":       True,
            "origin":   {"lat": lat, "lon": lon},
            "radius_m": radius_m,
            "n_alerts": len(alerts),
            "n_jams":   len(jams),
            "alerts":   alerts,
            "jams":     jams,
        })
    except KeyError as e:
        return jsonify({"ok": False, "message": f"Missing required param: {e}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/waze/status")
def api_waze_status():
    """Return counts and freshness of Waze data in MongoDB."""
    try:
        from datetime import datetime, timezone, timedelta
        n_alerts = db["waze_alerts"].count_documents({})
        n_jams   = db["waze_jams"].count_documents({})
        latest_a = db["waze_alerts"].find_one({}, {"fetched_at": 1, "_id": 0},
                                              sort=[("fetched_at", -1)])
        latest_j = db["waze_jams"].find_one({}, {"fetched_at": 1, "_id": 0},
                                            sort=[("fetched_at", -1)])
        return jsonify({
            "ok":              True,
            "n_alerts":        n_alerts,
            "n_jams":          n_jams,
            "last_alert_at":   latest_a.get("fetched_at") if latest_a else None,
            "last_jam_at":     latest_j.get("fetched_at") if latest_j else None,
        })
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/waze/refresh", methods=["POST"])
@login_required
def api_waze_refresh():
    """Trigger a single Waze feed fetch. Admin only."""
    if current_user.role != "admin":
        return jsonify({"ok": False, "message": "Admin only"}), 403
    import subprocess
    fetcher = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "waze_fetcher.py")
    )
    try:
        result = subprocess.run(
            ["python3", fetcher],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(fetcher),
            env={**os.environ, "MONGO_URI": MONGO_URI},
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return jsonify({"ok": True, "message": output.strip()[-300:]})
        return jsonify({"ok": False, "message": output.strip()[-400:]}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "message": "Waze fetch timed out (>60s)"}), 504
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Florida Rivers API
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/rivers/gauges")
def api_rivers_gauges():
    """All FL river gauges with current status. Optional ?category=Minor,Moderate,Major"""
    category_filter = request.args.get("category", "")
    county_filter   = request.args.get("county", "")
    query = {}
    if category_filter:
        cats = [c.strip() for c in category_filter.split(",") if c.strip()]
        query["flood_category"] = {"$in": cats}
    if county_filter:
        query["county"] = {"$regex": county_filter, "$options": "i"}
    docs = list(db.fl_river_gauges.find(query, {"_id": 0}).sort("flood_category", -1).limit(500))
    for d in docs:
        if isinstance(d.get("updated_at"), datetime):
            d["updated_at"] = d["updated_at"].isoformat()
    return jsonify({"count": len(docs), "gauges": docs})


@app.route("/api/rivers/gauge/<lid>")
def api_rivers_gauge_detail(lid):
    """Single gauge detail + last 24 h of readings."""
    gauge = db.fl_river_gauges.find_one({"lid": lid}, {"_id": 0})
    if not gauge:
        return jsonify({"error": "Gauge not found"}), 404
    if isinstance(gauge.get("updated_at"), datetime):
        gauge["updated_at"] = gauge["updated_at"].isoformat()

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    readings = list(db.fl_river_readings.find(
        {"lid": lid, "fetched_at": {"$gte": since}},
        {"_id": 0, "gage_height_ft": 1, "discharge_cfs": 1,
         "flood_category": 1, "stage_trend": 1, "fetched_at": 1},
    ).sort("fetched_at", 1).limit(200))
    for r in readings:
        if isinstance(r.get("fetched_at"), datetime):
            r["fetched_at"] = r["fetched_at"].isoformat()

    return jsonify({"gauge": gauge, "readings": readings})


@app.route("/api/rivers/flood")
def api_rivers_flood():
    """Gauges at or above Action stage only."""
    docs = list(db.fl_river_gauges.find(
        {"flood_category": {"$in": ["Action", "Minor", "Moderate", "Major", "Record"]}},
        {"_id": 0},
    ).sort([("flood_category", -1), ("name", 1)]))
    for d in docs:
        if isinstance(d.get("updated_at"), datetime):
            d["updated_at"] = d["updated_at"].isoformat()
    return jsonify({"flood_count": len(docs), "gauges": docs})


@app.route("/api/rivers/alerts")
def api_rivers_alerts():
    """Latest AI-generated river alert summaries."""
    limit = min(int(request.args.get("limit", 10)), 50)
    docs = list(db.fl_river_alerts.find(
        {}, {"_id": 0},
    ).sort("generated_at", -1).limit(limit))
    for d in docs:
        if isinstance(d.get("generated_at"), datetime):
            d["generated_at"] = d["generated_at"].isoformat()
    return jsonify({"count": len(docs), "alerts": docs})


@app.route("/api/rivers/status")
def api_rivers_status():
    """Quick dashboard status: gauge counts, last update, worst flood category."""
    total   = db.fl_river_gauges.count_documents({})
    at_flood = db.fl_river_gauges.count_documents(
        {"flood_category": {"$in": ["Action", "Minor", "Moderate", "Major", "Record"]}}
    )
    last_gauge = db.fl_river_gauges.find_one(
        {}, {"updated_at": 1, "_id": 0}, sort=[("updated_at", -1)]
    )
    last_alert = db.fl_river_alerts.find_one(
        {}, {"severity": 1, "summary_text": 1, "generated_at": 1, "_id": 0},
        sort=[("generated_at", -1)],
    )
    worst = db.fl_river_gauges.find_one(
        {"flood_category": {"$in": ["Major", "Moderate", "Minor", "Action"]}},
        {"flood_category": 1, "name": 1, "_id": 0},
        sort=[("flood_category", -1)],
    )
    result = {
        "total_gauges":      total,
        "at_flood_count":    at_flood,
        "last_fetch":        last_gauge["updated_at"].isoformat() if last_gauge and isinstance(last_gauge.get("updated_at"), datetime) else None,
        "worst_category":    worst["flood_category"] if worst else "Normal",
        "worst_gauge_name":  worst["name"] if worst else None,
        "last_ai_severity":  last_alert["severity"] if last_alert else None,
        "last_ai_summary":   last_alert["summary_text"][:200] if last_alert else None,
        "last_ai_generated": last_alert["generated_at"].isoformat() if last_alert and isinstance(last_alert.get("generated_at"), datetime) else None,
    }
    return jsonify(result)


@app.route("/api/rivers/agent/run", methods=["POST"])
@login_required
def api_rivers_agent_run():
    """Trigger an immediate AI agent analysis of river conditions (admin only)."""
    import subprocess, sys
    try:
        proc = subprocess.Popen(
            [sys.executable,
             "/home/ufuser/Fpren-main/weather_rss/fl_rivers_agent.py"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate(timeout=120)
        doc = db.fl_river_alerts.find_one(
            {}, {"_id": 0}, sort=[("generated_at", -1)]
        )
        if doc and isinstance(doc.get("generated_at"), datetime):
            doc["generated_at"] = doc["generated_at"].isoformat()
        return jsonify({"ok": True, "alert": doc})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# AI Agent API
# ─────────────────────────────────────────────────────────────────────────────

def _agent_available():
    """Return (ok, error_msg) — checks LiteLLM key is configured."""
    try:
        import sys as _sys
        root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
        if root not in _sys.path:
            _sys.path.insert(0, root)
        from weather_station.services.ai_client import is_configured
        if not is_configured():
            return False, "UF_LITELLM_API_KEY is not set"
        return True, ""
    except Exception as e:
        return False, str(e)


AGENT_SYSTEM_PROMPT = """\
You are the FPREN Operator Assistant — an AI embedded in the Florida Public Radio \
Emergency Network dashboard. You have access to tools that query live MongoDB data \
including NWS/IPAWS weather alerts, METAR observations, FL511 traffic incidents, \
Waze real-time alerts, US Census demographics, Florida hurricane evacuation zones \
and routes, and Icecast stream status. \
Answer concisely and factually, always using the tools to get current data. \
Do not make up numbers — call the appropriate tool. \
When done, give a clear plain-English answer.\
"""


@app.route("/api/agent/chat", methods=["POST"])
@login_required
def api_agent_chat():
    """POST {message: str} → {ok, response, tool_calls, iterations}"""
    ok, err = _agent_available()
    if not ok:
        return jsonify({"ok": False, "error": f"AI not configured: {err}"}), 503

    data    = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"ok": False, "error": "message is required"}), 400

    try:
        import sys as _sys
        root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
        if root not in _sys.path:
            _sys.path.insert(0, root)
        from weather_station.services.ai_client import run_agent
        from weather_rss.agent_tools import TOOL_SCHEMAS_READONLY, TOOL_FUNCTIONS

        result = run_agent(
            system_prompt   = AGENT_SYSTEM_PROMPT,
            tools           = TOOL_SCHEMAS_READONLY,
            tool_functions  = TOOL_FUNCTIONS,
            initial_message = message,
            max_iterations  = 8,
            max_tokens      = 512,
        )
        return jsonify({
            "ok":         True,
            "response":   result["response"],
            "tool_calls": result["tool_calls"],
            "iterations": result["iterations"],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/agent/situation")
def api_agent_situation():
    """GET — return the most recent saved situation report."""
    try:
        doc = db["situation_reports"].find_one({}, {"_id": 0},
                                               sort=[("generated_at", -1)])
        if not doc:
            return jsonify({"ok": False, "text": None})
        return jsonify({"ok": True, **doc})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/agent/situation/generate", methods=["POST"])
@login_required
def api_agent_situation_generate():
    """POST — run the situation awareness agent and return the new report. Admin only."""
    if current_user.role != "admin":
        return jsonify({"ok": False, "error": "Admin role required"}), 403
    ok, err = _agent_available()
    if not ok:
        return jsonify({"ok": False, "error": f"AI not configured: {err}"}), 503

    try:
        import sys as _sys
        root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
        if root not in _sys.path:
            _sys.path.insert(0, root)
        from weather_station.services.ai_client import run_agent
        from weather_rss.agent_tools import TOOL_SCHEMAS_WRITE, TOOL_FUNCTIONS
        from weather_rss.situation_agent import SYSTEM_PROMPT, TASK_PROMPT

        result = run_agent(
            system_prompt   = SYSTEM_PROMPT,
            tools           = TOOL_SCHEMAS_WRITE,
            tool_functions  = TOOL_FUNCTIONS,
            initial_message = TASK_PROMPT,
            max_iterations  = 12,
            max_tokens      = 600,
        )
        from datetime import datetime, timezone
        return jsonify({
            "ok":         True,
            "text":       result["response"],
            "tool_calls": result["tool_calls"],
            "iterations": result["iterations"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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


# -------------------- SYNC / STATE (bidirectional desktop ↔ web sync) ----------

@app.route("/api/sync")
def api_sync():
    """Lightweight endpoint for change detection. Returns a short hash token
    that changes whenever the shared dashboard state changes. Clients poll
    this every 5 s and only fetch full state when the token is different."""
    import hashlib
    state = _get_dash_state()
    tab  = state.get("active_tab", "weather")
    ts   = str(state.get("updated_at", ""))
    token = hashlib.md5(f"{tab}{ts}".encode()).hexdigest()[:8]
    return jsonify({"token": token, "active_tab": tab, "ts": ts})


@app.route("/api/state", methods=["GET"])
def api_state_get():
    """Return full shared dashboard state enriched with live counts."""
    state = _get_dash_state()
    # Count non-expired alerts
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000)
        now_iso = datetime.now(timezone.utc).isoformat()
        alert_count = client["weather_rss"]["nws_alerts"].count_documents(
            {"expires": {"$gt": now_iso}}
        )
        client.close()
    except Exception:
        alert_count = 0
    return jsonify({
        "active_tab":         state.get("active_tab", "weather"),
        "active_alert_count": alert_count,
        "last_broadcast_time": str(state.get("last_broadcast_time", "")),
        "pending_actions":    state.get("pending_actions", []),
        "updated_at":         str(state.get("updated_at", "")),
    })


@app.route("/api/state", methods=["POST"])
def api_state_post():
    """Accept state updates from the desktop app or web client."""
    body = request.get_json(silent=True) or {}
    updates = {}
    if "active_tab" in body:
        updates["active_tab"] = str(body["active_tab"])
    if "pending_actions" in body:
        updates["pending_actions"] = body["pending_actions"]
    if updates:
        _set_dash_state(updates)
    return jsonify({"ok": True})


# -------------------- RUN ------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
