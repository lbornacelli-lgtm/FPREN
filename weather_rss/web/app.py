from flask import Flask, redirect, render_template_string, request, url_for
from pymongo import MongoClient
from datetime import datetime, timezone

# -------------------- CONFIG --------------------
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "weather_rss"
COLLECTION = "feed_status"

STALE_THRESHOLD_MIN = 30  # feeds older than 30 minutes are considered stale

# -------------------- APP -----------------------
app = Flask(__name__)
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
status_col = db[COLLECTION]

# -------------------- TEMPLATE ------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Weather RSS Dashboard</title>
<style>
  body { font-family: Arial, sans-serif; margin: 20px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #999; padding: 8px; text-align: center; }
  th { background: #333; color: white; }
  .OK { background-color: #d4f8d4; }
  .STALE { background-color: #fff3cd; }
  .ERROR { background-color: #f8d7da; }
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
  .btn-primary { background: #0d6efd; color: #fff; border: none; padding: 6px 14px;
                 border-radius: 4px; cursor: pointer; }
  .btn-secondary { background: #6c757d; color: #fff; border: none; padding: 6px 14px;
                   border-radius: 4px; cursor: pointer; }
</style>
</head>
<body>
<div style="display:flex; justify-content:space-between; align-items:center;">
  <h1 style="margin:0 0 16px">Weather RSS Dashboard</h1>
  <button class="feedback-btn" onclick="document.getElementById('feedbackDialog').showModal()">Feedback</button>
</div>

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
    <td>{{ feed.last_success or "—" }}</td>
    <td>{{ feed.age_min or "—" }}</td>
    <td>{{ feed.file_size_kb or "—" }}</td>
    <td>{{ feed.status }}</td>
  </tr>
  {% endfor %}
</table>
</body>
</html>
"""

# -------------------- ROUTE ----------------------
@app.route("/")
def dashboard():
    feeds = []
    now = datetime.now(timezone.utc)

    for feed in status_col.find():
        last_success = feed.get("last_success")
        age_min = None
        row_class = "OK"

        if last_success:
            # convert last_success from ISODate / datetime
            if isinstance(last_success, str):
                last_success = datetime.fromisoformat(last_success)
            age_min = round((now - last_success).total_seconds() / 60, 1)

        status = feed.get("status", "UNKNOWN")

        # Mark STALE if too old
        if status == "OK" and age_min and age_min > STALE_THRESHOLD_MIN:
            row_class = "STALE"
        elif status == "ERROR":
            row_class = "ERROR"

        feeds.append({
            "filename": feed.get("filename", "—"),
            "last_success": last_success.strftime("%Y-%m-%d %H:%M:%S") if last_success else None,
            "age_min": age_min,
            "file_size_kb": feed.get("file_size_kb", "—"),
            "status": status,
            "row_class": row_class
        })

    return render_template_string(HTML_TEMPLATE, feeds=feeds)

# -------------------- FEEDBACK ------------------
@app.route("/feedback", methods=["POST"])
def submit_feedback():
    name = request.form.get("name", "").strip()
    message = request.form.get("message", "").strip()
    if not message:
        return redirect(url_for("dashboard") + "?msg=Feedback+message+is+required")
    db["feedback"].insert_one({
        "name": name or "Anonymous",
        "message": message,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    })
    return redirect(url_for("dashboard") + "?msg=Thank+you+for+your+feedback!")


# -------------------- RUN ------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
