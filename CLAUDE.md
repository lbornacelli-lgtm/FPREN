# CLAUDE.md — FPREN Master Context

FPREN (Florida Public Radio Emergency Network) is a 24/7 automated weather radio broadcast system serving Florida. It fetches NWS/IPAWS alerts, converts them to speech via TTS, manages a broadcast playlist per zone, and streams audio over Icecast. A Shiny dashboard provides real-time monitoring; a Flask app provides admin control.

> **Naming convention:** The full name is **Florida Public Radio Emergency Network**, abbreviated as **FPREN**.
> - Page `<title>` tags: `FPREN | Florida Public Radio Emergency Network`
> - Headers / descriptions: `FPREN — Florida Public Radio Emergency Network`
> - Stream metadata (`ice_name`): `FPREN Florida Public Radio Emergency Network`
> - Short labels, buttons, tabs, code identifiers: `FPREN` alone is fine

**Server:** `128.227.67.234` (UF VM, `/home/ufuser/Fpren-main`)
**Shiny dashboard:** `http://128.227.67.234` → port 3838 via Nginx
**Flask admin:** `http://128.227.67.234:5000` (login required)
**Icecast stream:** `http://128.227.67.234:8000/fpren`
**Git repo:** `https://github.com/lbornacelli-lgtm/FPREN.git`

---

## Active Services (systemd)

| Service | Source file | Purpose |
|---------|-------------|---------|
| `beacon-web-dashboard` | `weather_rss/web/app.py` | Flask admin dashboard (port 5000) |
| `beacon-station-engine` | `weather_station/main.py` | TTS + Icecast broadcast engine |
| `beacon-ipaws-fetcher` | `weather_rss/ipaws_fetcher.py` | NWS/IPAWS alert fetcher (2 min) |
| `beacon-obs-fetcher` | `weather_rss/weather_rss.py` | 19 FL ASOS station obs (15 min) |
| `beacon-extended-fetcher` | `weather_rss/extended_fetcher.py` | Extended forecast + FL511 traffic |
| `beacon-mongo-tts` | `mongo_tts/app.py` | MongoDB TTS monitor |
| `beacon-rivers-fetcher` | `weather_rss/fl_rivers_fetcher.py` | USGS FL river gauge data (15 min) |
| `beacon-rivers-agent` | `weather_rss/fl_rivers_agent.py` | LiteLLM agent river analysis (1 hr) |
| `zone-alert-tts` | `weather_station/services/zone_alert_tts.py` | Alert → MP3 per zone (main pipeline) |
| `fpren-broadcast-generator` | `weather_station/services/broadcast_generator.py` | AI broadcast scripts → MP3 per zone (every 30 min via timer) |
| `fpren-multi-zone-streamer` | `weather_station/services/multi_zone_streamer.py` | One FFmpeg → Icecast streamer per zone, all on port 8000 |
| `fpren_desktop` | `weather_rss/web/fpren_desktop.py` | Tkinter desktop mirror — syncs with web dashboard via `/api/sync` every 5 s |
| `icecast2` | system | Audio streaming server |
| `shiny-server` | `/srv/shiny-server/fpren/app.R` | Primary monitoring dashboard |
| `mongod` | system | MongoDB (`weather_rss` database) |
| `nginx` | `/etc/nginx/sites-available/fpren` | Reverse proxy (port 80 → 3838) |

---

## Repository Structure

```
Fpren-main/
├── CLAUDE.md                       ← You are here (master context)
├── weather_rss/                    # Fetcher pipeline + Flask admin
│   ├── CLAUDE.md                   ← Fetcher pipeline details
│   ├── ipaws_fetcher.py            # NWS/IPAWS FL alerts → MongoDB (2 min)
│   ├── weather_rss.py              # 19 FL ASOS obs (15 min)
│   ├── extended_fetcher.py         # Extended forecasts + FL511 traffic
│   ├── airport_delays_fetcher.py   # FAA airport delays
│   ├── alert_worker.py             # Alert rule processor
│   └── web/
│       ├── CLAUDE.md               ← Flask admin app details
│       └── app.py                  # Flask admin dashboard (port 5000)
├── weather_station/                # Broadcast engine
│   ├── CLAUDE.md                   ← Broadcast engine details
│   ├── main.py                     # Entry point → beacon-station-engine
│   ├── core/                       # Station loop, audio, playlist
│   └── services/                   # TTS, Icecast, AI, zone alert pipeline
├── shiny_dashboard/
│   ├── CLAUDE.md                   ← Dashboard deploy instructions
│   └── app.R                       # Deploy to /srv/shiny-server/fpren/
├── mongo_tts/
│   └── app.py                      # MongoDB TTS monitor (beacon-mongo-tts)
├── scripts/                        # One-off utilities (seed, monitor)
├── systemd/                        # Systemd unit templates
├── reports/                        # R report generation
└── logs/                           # Runtime log files
```

---

## MongoDB Collections (database: `weather_rss`)

| Collection | Purpose |
|------------|---------|
| `nws_alerts` | All active NWS/IPAWS/county alerts |
| `zone_alert_wavs` | Tracking records for generated audio files |
| `zone_definitions` | 9 zone configs (county lists + cleanup rules) |
| `fl_traffic` | FL511 traffic incidents |
| `waze_alerts` | Waze CCP point incidents (accidents, hazards, closures) with GeoJSON Point |
| `waze_jams` | Waze CCP polyline jams with GeoJSON LineString + centroid Point |
| `fl_river_gauges` | FL river gauge metadata + flood stage thresholds + current readings (513 gauges) |
| `fl_river_readings` | Time-series gage height + discharge per gauge (90-day TTL) |
| `fl_river_alerts` | LiteLLM agent-generated river condition summaries (30-day retention) |
| `airport_metar` | Current METAR obs for 19 FL ASOS stations (updated every 15 min) |
| `airport_delays` | FAA airport delay status |
| `weather_history` | Hourly METAR snapshots for 16 FL cities — temp, wind, humidity, flight cat (90-day retention) |
| `users` | Dashboard user accounts (bcrypt hashed, extended with email/phone/verification fields) |
| `feed_status` | RSS feed health status |
| `dashboard_state` | Singleton `_id:"singleton"` — shared active_tab between web + desktop for bidirectional sync |
| `user_audit_log` | Auth audit trail: login, logout, failed login, lock, add/delete user, password reset |
| `notification_config` | Singleton `_id:"singleton"` — comma-separated `notify_emails` for user management alerts |

---

## Zone Map (9 zones)

| Zone ID | Coverage | Audio cleanup |
|---------|----------|---------------|
| `all_florida` | Catch-all (thunderstorm/hurricane/flood filtered) | 24 h / max 10 files |
| `north_florida` | Counties north of Marion | 72 h |
| `central_florida` | Marion → Palm Beach (Tampa, Daytona) | 72 h |
| `south_florida` | Palm Beach south | 72 h |
| `tampa` | Hillsborough + Pinellas | 72 h |
| `miami` | Miami-Dade + Broward | 72 h |
| `orlando` | Orange + Osceola + Seminole | 72 h |
| `jacksonville` | Duval + Clay + St. Johns | 72 h |
| `gainesville` | Alachua | 72 h |

Audio lives at: `weather_station/audio/zones/<zone_id>/`

---

## TTS Stack

| Engine | Use case | Notes |
|--------|----------|-------|
| **Piper** | Primary — all regular alerts | Local, no rate limits. Voice: `en_US-amy-medium.onnx` |
| **ElevenLabs** | Critical alerts only | Tornado warning, hurricane warning, etc. |
| gTTS | Removed | No longer used |

---

## AI Integration (UF LiteLLM)

- **Endpoint:** `https://api.ai.it.ufl.edu`
- **Model:** `llama-3.3-70b-instruct`
- **Key:** `UF_LITELLM_API_KEY` in `weather_station/config/.env`
- **Uses:** alert severity classification, broadcast script generation, playlist decisions

---

## Ports

| Port | Service | Status |
|------|---------|--------|
| 80 | Nginx → Shiny | Pending UF IT firewall approval |
| 443 | Nginx HTTPS | Pending UF IT firewall approval |
| 3838 | Shiny Server | Active |
| 5000 | Flask admin | Active |
| 8000 | Icecast — all 9 zone mounts (`/fpren`, `/north-florida`, …) | Active (zone mounts blocked externally by UF IT) |
| 8001–8010 | Multi-zone Icecast (alternate ports) | Not used — all zones now on port 8000 |
| 8787 | RStudio Server | Active |
| 27017 | MongoDB | Internal only |

---

## NTP / Time Configuration

- **Timezone:** `America/New_York` (EDT/EST)
- **NTP service:** `systemd-timesyncd` (active, synchronized)
- **Primary NTP:** `128.227.30.254` (UF time server, Stratum 2)
- **Fallback NTP:** `time.nist.gov pool.ntp.org`
- **Config file:** `/etc/systemd/timesyncd.conf`

To check sync status: `timedatectl status` / `timedatectl show-timesync --all`
To restart: `sudo systemctl restart systemd-timesyncd`

---

## Common Commands

```bash
# Always activate venv first
cd ~/Fpren-main && source venv/bin/activate

# --- Service management ---
sudo systemctl status beacon-station-engine
sudo systemctl restart beacon-web-dashboard
sudo systemctl restart zone-alert-tts
sudo systemctl restart shiny-server

# Show all FPREN-related services at once
sudo systemctl list-units | grep -E "beacon|zone-alert|icecast|shiny|mongo"

# --- Deploy Shiny dashboard ---
sudo cp shiny_dashboard/app.R /srv/shiny-server/fpren/app.R
sudo chown shiny:shiny /srv/shiny-server/fpren/app.R
sudo systemctl restart shiny-server

# --- Stream health ---
curl -s http://localhost:8000/status-json.xsl | python3 -m json.tool

# --- MongoDB quick checks ---
mongosh weather_rss --eval "db.nws_alerts.countDocuments()"
mongosh weather_rss --eval "db.zone_definitions.countDocuments()"
mongosh weather_rss --eval "db.zone_alert_wavs.countDocuments()"

# --- Logs ---
sudo journalctl -u zone-alert-tts.service -f
sudo journalctl -u beacon-station-engine.service -f
sudo journalctl -u beacon-web-dashboard.service -f
sudo tail -f weather_rss/logs/web_dashboard.log

# --- Test Piper TTS ---
python3 -c "from weather_station.core.tts_service import TTSService; TTSService().say('Test', output_file='/tmp/t.mp3')"

# --- Seed zone definitions (run once or after DB reset) ---
python3 scripts/seed_zone_definitions.py
```

---

## Environment Variables (`weather_station/config/.env`)

```
MONGO_URI=mongodb://localhost:27017/
ZONES_ROOT=/home/ufuser/Fpren-main/weather_station/audio/zones
PIPER_VOICE_MODEL=/home/ufuser/Fpren-main/weather_station/voices/en_US-amy-medium.onnx
UF_LITELLM_BASE_URL=https://api.ai.it.ufl.edu
UF_LITELLM_API_KEY=sk-...
UF_LITELLM_MODEL=llama-3.3-70b-instruct
ELEVENLABS_API_KEY=sk-...
ICECAST_SOURCE_PASSWORD=fpren_source
```

---

## File Ownership Map

| Component | Primary owner files |
|-----------|-------------------|
| Alert ingestion | `weather_rss/ipaws_fetcher.py`, `weather_rss/weather_rss.py` |
| Alert → audio | `weather_station/services/zone_alert_tts.py` |
| Audio playback | `weather_station/core/audio_engine.py`, `icecast_streamer.py` |
| Playlist logic | `weather_station/core/playlist_engine.py`, `services/ai_playlist.py` |
| AI rewrite/classify | `weather_station/services/ai_classifier.py`, `ai_client.py` |
| Broadcast scripts | `weather_station/services/broadcast_generator.py` |
| Flask admin | `weather_rss/web/app.py` |
| Shiny dashboard | `shiny_dashboard/app.R` → deployed to `/srv/shiny-server/fpren/app.R` |
| Zone config | `weather_station/config/settings.py`, `scripts/seed_zone_definitions.py` |

---

## Gotchas

- **Shiny edits** must be copied to `/srv/shiny-server/fpren/app.R` — editing `shiny_dashboard/app.R` alone does nothing until deployed.
- **Zone audio** is generated by `zone-alert-tts` service, not by `beacon-station-engine`. They are separate services.
- **Multi-zone streams** all run on port 8000 via separate Icecast mount points (`/fpren`, `/north-florida`, `/orlando`, etc). UF IT blocks external access to all zone mounts except `/fpren`. Internally they all work. Do not change the port — the alternate port approach (8001–8010) was abandoned.
- **venv** must be active for all Python commands. Located at `~/Fpren-main/venv/`.
- **Flask login** uses MongoDB `users` collection with bcrypt. No default admin — seed via `mongosh` if locked out.
- **Piper binary** must be on PATH. It is installed system-wide, not in venv.
- **ElevenLabs** is only triggered for tornado/hurricane warnings. Never use it for routine obs — it costs money.
- **`ai_classifier.py`** is wired into `zone_alert_tts.py` — called once per alert before the zone loop; falls back to Piper on failure.
- **`beacon-airport-delays`** service is enabled and running. FAA API (`soa.smext.faa.gov`) is unreachable from the UF VM due to outbound DNS/firewall restrictions — airport data stays empty until UF IT resolves this.

---

## Security Features (added 2026-04-02)

### Shiny Dashboard Authentication
The Shiny dashboard (`shiny_dashboard/app.R`, deployed to `/srv/shiny-server/fpren/app.R`) now requires user login. Previously it was publicly accessible.

**Key components:**
- Full-page login screen with UF/FPREN branding and AUP disclaimer (verbatim UF Acceptable Use Policy)
- Account lockout after 3 failed attempts (24-hour lock, `locked_until` field in MongoDB)
- 6-month inactivity auto-disable (checks `last_login` on every login)
- 2-minute inactivity warning / 3-minute auto-logout via JS `setInterval`
- First-login password change enforcement (`must_change_password` flag)
- SMS phone verification via Twilio after first password change
- Email verification via SMTP after phone verification
- Welcome email after email verification (explains 6-month inactivity policy)
- Forgot username/password flow (email-based 6-digit reset code)
- Full audit log in `user_audit_log` MongoDB collection

**Admin-only features (Config tab):**
- Enhanced user table: username, email, phone, role, active, email_verified, phone_verified, last_login, created_at, created_by
- Add User form: email + phone required; invite email sent with temp password
- Delete User with confirm modal and audit logging
- Notification email config (stored in `notification_config` collection)

**New R packages required:**
```r
sudo Rscript -e "install.packages(c('shinyjs','digest','emayili'), repos='https://cran.rstudio.com/')"
```

**New MongoDB collections:** `user_audit_log`, `notification_config`

**Extended `users` collection fields:** `email`, `phone`, `email_verified`, `phone_verified`, `must_change_password`, `failed_attempts`, `locked_until`, `last_login`, `created_by`, `invite_token`, `verify_code`, `verify_expires`, `reset_code`, `reset_expires`

### email_utils.py HTML Email Support
Added `send_html_email(subject, body_html, to=None)` to `weather_rss/email_utils.py`. Sends HTML emails with the UF FPREN banner footer. The `UF_BANNER_HTML` constant is also exported for use in other Python modules.

---

## Business Continuity Plan (BCP) System (added 2026-04-03)

### User Assets / Properties
Each MongoDB `users` document now supports an optional `assets` array:
```json
{
  "assets": [{
    "asset_id": "uuid-string",
    "asset_name": "WUFT Studio B",
    "address": "1600 SW 23rd Dr, Gainesville, FL 32608",
    "lat": 29.6516,
    "lon": -82.3248,
    "zip": "32608",
    "city": "Gainesville",
    "nearest_airport_icao": "KGNV",
    "nearest_airport_name": "Gainesville Regional Airport",
    "asset_type": "Radio Station",
    "notes": "",
    "created_at": "2026-04-03T14:00:00Z"
  }]
}
```

### Flask API Routes Added (`weather_rss/web/app.py`)
| Method | Route | Description |
|--------|-------|-------------|
| GET    | `/api/users/<username>/assets` | List user's assets |
| POST   | `/api/users/<username>/assets` | Add asset to user |
| PUT    | `/api/users/<username>/assets/<asset_id>` | Update asset |
| DELETE | `/api/users/<username>/assets/<asset_id>` | Remove asset |
| GET    | `/api/lookup/city-by-zip?zip=XXXXX` | ZIP → county, city, lat/lon, nearest airport ICAO |
| POST   | `/api/reports/generate-bcp` | Generate BCP PDF for a user asset |

### BCP R Markdown Template
- **File:** `reports/business_continuity_report.Rmd`
- **Parameters:** username, asset_name, address, lat, lon, zip, city, nearest_airport_icao, nearest_airport_name, asset_type, notes, mongo_uri, days_back
- **Sections:** Cover page, Executive Summary (risk table), Asset Location, Weather Risk (flight category chart), Alert History, Traffic/Evacuation, Airport Status, Recommendations, Recovery Timeline, Emergency Contacts

### 2PM Comprehensive Report (systemd timer)
- **Script:** `reports/generate_comprehensive_2pm.R`
- **Generates:** Alert summary + weather trends for all 16 cities + BCP for all user assets
- **Timer:** `systemd/fpren-comprehensive-2pm.timer` (runs daily at 14:00 ET)
- **Service:** `systemd/fpren-comprehensive-2pm.service`
- **Install:** `sudo bash systemd/install_2pm_timer.sh`

### Shiny Dashboard Updates (2026-04-03)
- **Reports tab:** New "Business Continuity Plans" section — user+asset selector, BCP generation, recent BCP list
- **Config tab:** New "User Assets / Properties" panel — view/add/delete assets per user with ZIP→city/airport lookup
- **Weather cards:** 7-day NWS forecast hover popup on each city card (JS + NWS API, 300ms delay, cached)
- **Playlist:** Drag-to-reorder priority ordering (SortableJS CDN) + priority saved to `zone_definitions.normal_mode_types`
- **Emails:** Polished HTML invite email; `send_fpren_email()` accepts optional `attachment_path`

---

## Waze for Cities Integration (added 2026-04-03)

### Overview
Pulls real-time traffic alerts, jams, and irregularities from the Waze Connected Citizens Program (CCP) JSON feed every 2 minutes and stores them in MongoDB with 2dsphere geospatial indexes so RStudio can run distance calculations against asset locations.

### Feed URL Configuration
Set one of:
- Env var: `WAZE_FEED_URL`
- Config file: `weather_rss/config/waze_config.json` → `{ "feed_url": "https://www.waze.com/row-partnerhub-api/partners/NNNNN/waze-feeds/TOKEN?format=1" }`

### Files Added
| File | Purpose |
|------|---------|
| `weather_rss/waze_fetcher.py` | CCP feed fetch + MongoDB upsert (--loop, --dry-run, --verbose) |
| `weather_rss/config/waze_config.json` | Feed URL config stub |
| `systemd/beacon-waze-fetcher.service` | Continuous loop service (2-min poll) |
| `scripts/waze_distance_helpers.R` | RStudio helper: distance queries + sf spatial objects |

### MongoDB Collections
| Collection | Key fields |
|------------|-----------|
| `waze_alerts` | `uuid`, `type`, `subtype`, `street`, `city`, `reliability`, `confidence`, `location` (GeoJSON Point), `lat`, `lon`, `pub_millis`, `fetched_at` |
| `waze_jams` | `uuid`, `street`, `city`, `speed_kmh`, `delay_sec`, `length_m`, `level` (0–5), `line` (GeoJSON LineString), `location` (centroid Point), `lat`, `lon`, `pub_millis`, `fetched_at` |

Indexes: `uuid` unique, `location` 2dsphere, `type`/`level`/`pub_millis`/`city`

### Flask API Routes
| Route | Description |
|-------|-------------|
| `GET /api/waze/alerts?type=&city=&hours=2&limit=500` | Recent alerts with optional filters |
| `GET /api/waze/jams?city=&min_level=&hours=2&limit=300` | Recent jams with optional filters |
| `GET /api/waze/nearby?lat=&lon=&radius_m=10000&hours=2` | Alerts + jams within radius via $nearSphere |
| `GET /api/waze/status` | Alert/jam counts + freshness timestamps |
| `POST /api/waze/refresh` | Trigger on-demand fetch (admin only) |

### RStudio Usage
```r
source("scripts/waze_distance_helpers.R")
asset <- c(lon = -82.3248, lat = 29.6516)  # WUFT Gainesville

waze_alerts_near(asset, radius_km = 10)
waze_jams_near(asset, radius_km = 15, min_level = 2)
waze_summary_near(asset, radius_km = 20)   # for BCP reports
```

### Systemd Setup
```bash
sudo cp systemd/beacon-waze-fetcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now beacon-waze-fetcher
sudo journalctl -u beacon-waze-fetcher -f
```

---

## Known Issues / TODO

- [ ] Port 80/443 and zone stream mounts pending UF IT firewall approval
- [ ] FAA API unreachable from UF VM — `beacon-airport-delays` runs but `airport_delays` MongoDB collection stays empty
- [x] Wire `ai_classifier` into `zone_alert_tts.py` main pipeline
- [x] Fix Flask dashboard tab loading issue (commit 6194bf8)
- [x] Desktop app (`weather_rss/web/fpren_desktop.py`) tab sync (commit 5d2db81)
- [x] Add authentication gate to Shiny dashboard (2026-04-02)
- [x] Add account lockout, inactivity timeout, first-login flow, SMS/email verification to Shiny (2026-04-02)
- [x] User assets/properties system + BCP reports (2026-04-03)
- [x] 2PM comprehensive daily report scheduler (2026-04-03)
- [x] 7-day forecast hover on weather city cards (2026-04-03)
- [x] Drag-to-reorder playlist priority (2026-04-03)
- [x] FL Census data integration + LiteLLM AI analysis (2026-04-03)
- [x] Waze for Cities CCP feed integration + RStudio distance helpers (2026-04-03)

---

## Florida Census Data Integration (added 2026-04-03)

### Data Source
- **API:** US Census Bureau ACS 5-Year Estimates (`api.census.gov/data/2022/acs/acs5`)
- **Coverage:** All 67 Florida counties
- **Variables:** Population, age 65+, under-18, poverty, limited English, disability, housing, median income
- **Key:** `weather_rss/config/census_config.json` → `{ "api_key": "..." }` (or `CENSUS_API_KEY` env var)

### MongoDB Collection: `fl_census`
Key fields: `county`, `fips_county`, `year`, `population_total`, `pct_65plus`, `pct_poverty`, `pct_limited_english`, `pct_disability`, `vulnerability_score` (0–1), `vulnerability_label` (Low/Moderate/High/Critical)

**Index:** `{ county: 1, year: -1 }` (unique)

### Files Added
| File | Purpose |
|------|---------|
| `weather_rss/fl_census_fetcher.py` | Pulls ACS data, computes vulnerability scores, upserts to MongoDB |
| `weather_rss/census_ai_analyzer.py` | LiteLLM-powered vulnerability/impact/BCP analysis via `ai_client.chat()` |
| `weather_rss/config/census_config.json` | Census API key storage |
| `systemd/beacon-census-fetcher.service` | oneshot service for on-demand fetch |
| `systemd/fpren-census-refresh.timer` | Monthly refresh timer |
| `systemd/install_census.sh` | First-time install script |

### Flask API Routes Added (`weather_rss/web/app.py`)
| Route | Description |
|-------|-------------|
| `GET /api/census/counties` | All 67 counties sorted by vulnerability score |
| `GET /api/census/county/<name>` | Single county full record |
| `GET /api/census/analysis/<name>?mode=vulnerability\|impact\|bcp&asset=<name>` | LiteLLM AI analysis |
| `GET /api/census/impact/<alert_id>` | Census-enriched alert with AI population impact |
| `POST /api/census/refresh` | Admin-only: trigger live Census API fetch |

### Shiny Dashboard
- New **Census & Demographics** tab (viewer role, sidebar menu)
- Value boxes: FL total population, avg % 65+, avg % poverty, high-vulnerability county count
- County selector with demographic detail panel + vulnerability bar chart
- AI analysis button → LiteLLM vulnerability narrative
- Active alert impact panel → population at risk + AI assessment
- Full 67-county DT table sorted by vulnerability score

### LiteLLM Integration
`census_ai_analyzer.py` uses `weather_station.services.ai_client.chat()` with three modes:
1. **`analyze_county_vulnerability(county)`** — demographic vulnerability narrative
2. **`analyze_alert_impact(county, alerts, census)`** — population impact of active alerts
3. **`analyze_bcp_demographics(county, asset_name)`** — BCP-specific recommendations

All three fall back to rule-based summaries if `UF_LITELLM_API_KEY` is not set or AI call fails.

### BCP Report Enhancement
`reports/business_continuity_report.Rmd` now includes a **Population & Vulnerability Analysis** section:
- Census stats table (population, elderly %, poverty %, LEP %, disability %, income, housing)
- Vulnerability bar chart (5 population groups)
- AI-generated demographic narrative (calls `census_ai_analyzer.py` via Python subprocess)

### Setup
```bash
# 1. Add your Census API key
nano weather_rss/config/census_config.json

# 2. Run install script (seeds DB + enables monthly timer)
sudo bash systemd/install_census.sh

# 3. Verify data loaded
mongosh weather_rss --eval "db.fl_census.countDocuments()"
# Should return 67
```

---

## Subdirectory CLAUDE.md Files

Each major subdirectory has its own CLAUDE.md with component-specific context:

- [`weather_rss/CLAUDE.md`](weather_rss/CLAUDE.md) — fetcher pipeline, data sources, MongoDB writes
- [`weather_station/CLAUDE.md`](weather_station/CLAUDE.md) — broadcast engine, TTS, zone audio, Icecast
- [`shiny_dashboard/CLAUDE.md`](shiny_dashboard/CLAUDE.md) — Shiny app, deploy workflow
- [`weather_rss/web/CLAUDE.md`](weather_rss/web/CLAUDE.md) — Flask admin, auth, all API routes

> **Rule:** Every new feature added to this project must include a corresponding update to the relevant subdirectory CLAUDE.md file.

---

## SNMP Monitoring (added 2026-04-05)

### SNMP Agent
- **OID base:** `1.3.6.1.4.1.64533` (FPREN private enterprise — register with IANA if public deployment needed)
- **Community string:** `fpren_monitor`
- **pass_persist script:** `scripts/fpren_snmp_agent.py` (invoked via shell wrapper `scripts/run_fpren_snmp.sh`)
- **Standalone updater:** `scripts/fpren_snmp_update.py` — run by `fpren-snmp-updater.timer` every 60s; writes to MongoDB `fpren_snmp_status`
- **Extend OIDs:** `fprenHealth`, `fprenAlerts`, `fprenServices`, `fprenWxCat`, `fprenListeners`, `fprenUpdated`
- **MIB file:** `scripts/fpren_mib.txt` (also at `/usr/share/snmp/mibs/FPREN-MIB.txt`)
- **snmpd.conf:** `/etc/snmp/snmpd.conf`
- **Test:** `snmpwalk -v2c -c fpren_monitor localhost .1.3.6.1.4.1.64533.1`
- **snmp_query helper:** `scripts/snmp_query.sh <field>` — returns single field from MongoDB `fpren_snmp_status`

### OID Tree Summary
| OID | Description |
|-----|-------------|
| `.1.3.6.1.4.1.64533.1.1.0` | systemHealth (OK/DEGRADED/CRITICAL) |
| `.1.3.6.1.4.1.64533.1.2.0` | activeAlertCount |
| `.1.3.6.1.4.1.64533.1.5.0` | worstFlightCat |
| `.1.3.6.1.4.1.64533.1.6.0` | icecastListeners |
| `.1.3.6.1.4.1.64533.1.7.0` | mongodbStatus |
| `.1.3.6.1.4.1.64533.1.10.0` | activeServiceCount |
| `.1.3.6.1.4.1.64533.2.1.3.N` | serviceStatus for service N (1–11) |
| `.1.3.6.1.4.1.64533.4.U.A` | User U, Asset A OID address |

### New systemd units
- `fpren-snmp-updater.service` + `.timer` — runs `fpren_snmp_update.py` every 60s as `ufuser`

---

## Emergency SMS System (added 2026-04-05)

### MongoDB collections
- `emergency_roles_config` — `{_id: "role|phase", role, phase, todos: [], updated_at}`. Seeded for 4 roles × 3 phases.

### User field
- `users.sms_emergency_enabled` (boolean) — admin-controlled per-user SMS opt-in.

### CLI tool
`weather_rss/emergency_sms.py --phones <csv> --role <role> --phase <before|during|after>`
Formats numbered SMS from MongoDB todos, sends via Twilio. Supports `--dry-run`.

---

## BCP Report Enhancements (added 2026-04-05)

- New `profession` param in `business_continuity_report.Rmd`
- Waze accident/jam hotspot section (15 km radius, last 6 h)
- FL county EM offices with addresses + phones (9 counties)
- State/federal agency contacts table
- Role-specific contacts per profession
- SMS Emergency Action Checklist section (3-column: before/during/after)
