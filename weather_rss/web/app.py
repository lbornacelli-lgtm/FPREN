import os
from flask import Flask, redirect, render_template_string, request, url_for
from pymongo import MongoClient
from datetime import datetime, timezone

# -------------------- CONFIG --------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "weather_rss"
COLLECTION = "feed_status"

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
app = Flask(__name__)
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
<title>Beacon Alerts Dashboard</title>
<style>
  body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
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
</style>
</head>
<body>

<div style="display:flex; justify-content:space-between; align-items:center;">
  <h1 style="margin:0 0 4px">Beacon Alerts Dashboard</h1>
  <button class="feedback-btn" onclick="document.getElementById('feedbackDialog').showModal()">Feedback</button>
</div>
<small>Auto-refreshes every 60 seconds &mdash; {{ now }}</small>

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
    <th>Temp °C</th>
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
    <td class="center">{{ ap.temp }}</td>
    <td class="center">{{ ap.dewp }}</td>
    <td class="center">{{ ap.wdir }}</td>
    <td class="center">{{ ap.wspd }}</td>
    <td class="center">{{ ap.visib }}</td>
    <td><small>{{ ap.rawOb }}</small></td>
    <td class="center">{{ ap.obsTime }}</td>
  </tr>
  {% else %}
  <tr><td colspan="10" class="no-data">No METAR data</td></tr>
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
        airports.append({
            "icaoId":   ap.get("icaoId", ""),
            "name":     ap.get("name", ""),
            "fltCat":   flt_cat,
            "flt_class": FLTCAT_CLASS.get(flt_cat, ""),
            "temp":     ap.get("temp", ""),
            "dewp":     ap.get("dewp", ""),
            "wdir":     ap.get("wdir", ""),
            "wspd":     ap.get("wspd", ""),
            "visib":    ap.get("visib", ""),
            "rawOb":    ap.get("rawOb", ""),
            "obsTime":  obs,
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

    return render_template_string(
        HTML_TEMPLATE,
        feeds=feeds,
        alerts=alerts,
        airports=airports,
        traffic=traffic,
        school=school,
        now=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    )

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
