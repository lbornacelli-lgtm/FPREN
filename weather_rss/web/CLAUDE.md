# CLAUDE.md — weather_rss/web (Flask Admin Dashboard)

This is the Flask admin dashboard for FPREN operators. It provides stream control, alert management, user management, content upload, and system monitoring. See root [`CLAUDE.md`](../../CLAUDE.md) for system context.

---

## Overview

| Item | Value |
|------|-------|
| Entry point | `app.py` |
| Service | `beacon-web-dashboard` |
| Port | `5000` |
| URL | `http://128.227.67.234:5000` |
| Auth | Flask-Login + MongoDB `users` collection (bcrypt passwords) |

---

## Auth System

- **Library:** `flask-login` (`LoginManager`, `UserMixin`)
- **Storage:** MongoDB `users` collection — passwords bcrypt-hashed
- **Login view:** `/login` (POST form, redirects to `/` on success)
- **Protected routes:** All `/api/*` user management routes require `@login_required`
- **Session:** Cookie-based via Flask secret key

Most API routes are **not** login-protected (stream control, weather data, etc.) — only user management endpoints require auth.

To add an admin user from the shell:
```bash
python3 -c "
from pymongo import MongoClient
import bcrypt
db = MongoClient()['weather_rss']
db.users.insert_one({'username':'admin','password': bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode()})
"
```

---

## API Routes Reference

### Auth
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET/POST | `/login` | — | Login form |
| GET | `/logout` | ✓ | Logout + redirect |
| GET | `/` | ✓ | Main dashboard SPA |

### Streams
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/streams` | — | List all streams + status |
| POST | `/api/streams/<id>/zone` | — | Override zone for stream |
| POST | `/api/streams/<id>/stop` | — | Stop a stream |
| POST | `/api/streams/start-engine` | — | Start broadcast engine |
| POST | `/api/streams/restart-engine` | — | Restart broadcast engine |

### Weather & Alerts
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/weather` | — | Current obs + active alerts |
| GET | `/api/icecast` | — | Icecast stream status |
| GET | `/api/airports` | — | Airport delay data |
| GET | `/api/data-tab` | — | Combined data for dashboard tab |

### Playlist & Audio
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/playlist` | — | Current playlist state |
| POST | `/api/playlist/mute/toggle` | — | Toggle mute for a stream |
| POST | `/api/playlist/<filename>/slots` | — | Update slot assignments |
| POST | `/api/playlist/assign` | — | Assign content to playlist slot |
| GET | `/api/zone-audio` | — | Zone audio file listing |
| GET | `/alerts/<alert_id>/wav` | — | Download alert audio file |
| GET | `/audio/download/<doc_id>` | — | Download content library file |
| GET | `/api/transcode/status` | — | Transcode job status |
| POST | `/api/transcode/run` | — | Run transcode job |
| POST | `/api/upload` | — | Upload content file |
| GET | `/api/upload/list` | — | List uploaded content files |
| POST | `/api/upload/delete` | — | Delete uploaded file |

### Zones
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/zones` | — | Zone definitions |

### AI
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/ai/rewrite-alert` | — | AI rewrite of alert text |
| POST | `/api/ai/broadcast` | — | Generate broadcast script |

### Reports
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/reports/alert-events` | — | Alert event history |
| GET | `/api/reports/list` | — | List generated reports |
| GET | `/api/reports/download/<filename>` | — | Download a report file |
| POST | `/api/reports/generate` | — | Trigger report generation |

### SMTP
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/smtp` | — | Get SMTP config |
| POST | `/api/smtp` | — | Update SMTP config |
| POST | `/api/smtp/test` | — | Send SMTP test email |
| POST | `/api/stream-alert/test` | — | Send stream alert test email |

### User Management (auth required)
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/users` | ✓ | List users |
| POST | `/api/users/add` | ✓ | Add user |
| POST | `/api/users/delete` | ✓ | Delete user |
| POST | `/api/users/password` | ✓ | Change user password |

### Other
| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| POST | `/feedback` | — | Submit operator feedback |

---

## Key Config at Top of `app.py`

```python
ZONE_OVERRIDES_FILE = "/home/ufuser/Fpren-main/weather_station/config/stream_zone_overrides.json"
SMTP_CFG_FILE       = "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"
AVAILABLE_ZONES     = ["all_florida", "north_florida", "central_florida", "south_florida", "miami", "jacksonville", "orlando", "tampa"]
STREAMS             = [...]  # 5 streams (ports 8000–8004)
```

---

## Other Files in This Directory

| File | Purpose |
|------|---------|
| `app.py` | Main Flask application (2900+ lines — monolith) |
| `fpren_desktop.py` | Desktop Tkinter GUI (separate app, not a service) |
| `static/` | CSS, JS, images for the dashboard SPA |
| `test_feedback.py` | Test script for feedback endpoint |

---

## Common Commands

```bash
# Service management
sudo systemctl status beacon-web-dashboard
sudo systemctl restart beacon-web-dashboard

# View Flask logs
sudo journalctl -u beacon-web-dashboard.service -f
sudo tail -f /home/ufuser/Fpren-main/weather_rss/logs/web_dashboard.log

# Quick health check
curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/

# Test AI rewrite endpoint
curl -s -X POST http://localhost:5000/api/ai/rewrite-alert \
  -H "Content-Type: application/json" \
  -d '{"headline":"Tornado Warning","area":"Alachua County","description":"A tornado warning is in effect."}'
```

---

## Gotchas

- `app.py` is a 2900+ line monolith — all routes are in one file. Be careful with edits; search for route before assuming it doesn't exist.
- Zone overrides are persisted to `stream_zone_overrides.json` on disk, not in MongoDB. The file is read on startup.
- SMTP config is persisted to `smtp_config.json`. Editing the file directly works but prefer the API.
- The Flask app reads Icecast status by hitting `http://localhost:8000/status-json.xsl` — if Icecast is down, `/api/icecast` will return partial data.
- `fpren_desktop.py` is a standalone Tkinter app, not served by the Flask service. Run it locally.

## Bidirectional Sync Architecture (added 2026-04-01)

The web dashboard and desktop Tkinter app maintain a shared active-tab state via MongoDB `weather_rss.dashboard_state` (singleton document `_id: "singleton"`).

### Flask endpoints added
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/sync` | Lightweight poll: returns `{token, active_tab, ts}`. Token is an 8-char MD5 hash of active_tab + updated_at. Changes whenever state changes. |
| GET | `/api/state` | Full state: `{active_tab, active_alert_count, last_broadcast_time, pending_actions, updated_at}`. |
| POST | `/api/state` | Accept `{active_tab?, pending_actions?}` from any client. Upserts MongoDB document and advances the token. |

### Web dashboard behaviour
- `showTab()` posts the new tab to `/api/state` on every user-initiated tab click.
- A `setInterval(_pollSync, 5000)` polls `/api/sync` every 5 s. When the token changes it switches to the remote tab.
- A `_syncSwitching` guard prevents the resulting `showTab()` call from echo-posting back to the server.

### Desktop app behaviour
- `<<NotebookTabChanged>>` binding calls `_on_tab_changed()` which posts the new tab to `/api/state` (suppressed during remote-driven switches via `_suppress_tab_event`).
- A daemon thread (`_start_sync_poll`) polls `/api/sync` every 5 s. When the token changes and the remote tab differs from the current tab, `_switch_to_tab(idx)` is called on the main thread via `after(0, ...)`.
- Full data refresh (`_refresh()`) still runs every 30 s.

### Tab mapping
| Web tab name | Desktop notebook index |
|---|---|
| weather | 0 |
| playlist | 1 |
| icecast | 2 |
| data | 3 |
| reports | 5 |
| config | 7 |
| airports, zones, upload, ai | no desktop equivalent — ignored |

## Frontend Tab Behaviour (fixed 2026-04-01)

- **Default tab is Weather.** The `localStorage` restore in the IIFE skips `config` and any unknown tab — it defaults to `weather` so slow config loaders never fire on page load.
- **Config loaders are deferred.** `loadConfig()`, `loadStreamControl()`, `loadSmtp()`, and `loadUsers()` only fire when the user clicks the Config tab — never on page load.
- **Page load calls only `loadWeather()` and `initUpload()`.**
- **Global 10-second fetch timeout.** A fetch wrapper in the IIFE overrides `window.fetch` so every API call aborts after 10 s, preventing a hung endpoint from freezing the UI.
- **Login always redirects to `/`** (Weather tab) — the `next` param is ignored to prevent stale redirect loops.
