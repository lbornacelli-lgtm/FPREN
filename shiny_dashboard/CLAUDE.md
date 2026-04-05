# CLAUDE.md — shiny_dashboard

This directory contains the Shiny app source for the FPREN primary monitoring dashboard. See root [`CLAUDE.md`](../CLAUDE.md) for system context.

---

## Critical: How Shiny Deploys

**Editing `shiny_dashboard/app.R` does NOT update the live dashboard.**

The live dashboard is served from a separate deployed path:
```
/srv/shiny-server/fpren/app.R   ← what Shiny Server actually reads
```

You must copy and restart after every edit:

```bash
sudo cp shiny_dashboard/app.R /srv/shiny-server/fpren/app.R
sudo chown shiny:shiny /srv/shiny-server/fpren/app.R
sudo systemctl restart shiny-server
```

Or use the slash command: `/deploy-shiny`

---

## Authentication & Security System

Added 2026-04-02. The Shiny dashboard now requires login before displaying any content.

### Architecture

- **Login gate:** A full-page login screen (`div#login_screen`) overlays the dashboard (`div#main_dashboard`) on load. `shinyjs::hide/show` toggles between them.
- **Auth state:** `auth_rv` reactiveValues stores `logged_in`, `username`, `role`, `email`, `phone`, `user_doc`.
- **Admin visibility:** `output$is_admin` reactive (used with `conditionalPanel`) hides user management from non-admin users.

### Login Security
- Tracks `failed_attempts` per user in MongoDB `users` collection.
- 3 failed attempts → account locked for 24 hours (`locked_until` field).
- Auto-unlocks when `locked_until` is in the past.
- Checks 6-month inactivity on login: sets `active: FALSE` and blocks login if `last_login` > 183 days ago.
- Updates `last_login` and resets `failed_attempts` on successful login.
- Every login event (success/fail/lock) is logged to `user_audit_log` MongoDB collection.

### Inactivity Timeout
- JavaScript `setInterval` (1-minute ticks) tracks mouse/keyboard/click/scroll activity.
- 2 minutes idle: fires `idle_warn` Shiny input → shows "Are you still there?" modal.
- 3 minutes idle: fires `idle_logout` Shiny input → forces logout, shows login screen.
- Timer does not tick while login screen is visible.

### Post-Login Flow (sequential modals)
1. `must_change_password == TRUE` → password change modal (requires 8+ chars, matching)
2. `phone_verified == FALSE` → SMS verification modal (6-digit code via Twilio, 10-min expiry)
3. `email_verified == FALSE` → email verification modal (6-digit code via SMTP, 10-min expiry)
4. After email verified → welcome email sent explaining 6-month inactivity policy

### Forgot Password Flow
- "Forgot username or password?" link on login screen opens a modal.
- User enters email → system sends username + 6-digit reset code (1-hour expiry).
- Second modal accepts reset code + new password → updates MongoDB, clears `reset_code`.

### AUP Disclaimer
The University of Florida Acceptable Use Policy text is displayed verbatim below the login form (hardcoded as `AUP_TEXT` constant).

### New R Libraries Required
- `shinyjs` — show/hide elements, run JS
- `digest` — imported but available for future token hashing
- `emayili` — HTML email sending (was already needed, now primary email method)

### Installation Commands
```r
sudo Rscript -e "install.packages(c('shinyjs','digest','emayili'), repos='https://cran.rstudio.com/')"
```

### MongoDB Collections Used by Auth
| Collection | Purpose |
|---|---|
| `users` | Extended with `email`, `phone`, `email_verified`, `phone_verified`, `must_change_password`, `failed_attempts`, `locked_until`, `last_login`, `created_by`, `invite_token`, `verify_code`, `verify_expires`, `reset_code`, `reset_expires` |
| `user_audit_log` | Audit trail: `{action, target_user, performed_by, timestamp, details}` |
| `notification_config` | `{_id:"singleton", notify_emails: "addr1,addr2"}` |

### Enhanced User Management (Admin Only)
- Admin-only section in Config tab (hidden via `conditionalPanel(condition="output.is_admin")`).
- Add User form: email + phone required; username auto-derived from email prefix.
- On add: generates 8-char temp password, bcrypt-hashes it, sets `must_change_password: TRUE`, sends invite email with temp credentials.
- Delete User: confirm modal, then removes from MongoDB, logs audit event, notifies configured emails.
- Notification emails: comma-separated list stored in `notification_config` collection; notified on add/delete.

---

## Tab Order and Descriptions

| # | Label | tabName | Description |
|---|-------|---------|-------------|
| 1 | Overview | `overview` | Value boxes + active alerts summary + station status |
| 2 | Weather Conditions | `wx_cities` | Card grid of current METAR obs for 16 FL cities |
| 3 | FL Alerts | `alerts` | All active NWS/IPAWS alerts with severity/source filters |
| 4 | Traffic Alerts | `traffic_alerts` | FL511 traffic incidents with county/severity/type filters |
| 4b | Traffic Analysis | `traffic_analysis` | Interactive statistical analysis of fl_traffic data — hotspot charts, county map, severity breakdown, CSV export |
| 5 | County Alerts | `county_alerts` | ZIP/county search for per-county alerts + PDF/email export |
| 6 | Airport Delays & Weather | `airports` | FAA delay status merged with METAR obs for FL airports |
| 7 | Upload Content | `upload` | Upload MP3/WAV to broadcast content folders |
| 8 | Reports | `reports` | Generate + email PDF alert summary reports |
| 9 | Station Health | `health` | Recent audio files + system/MongoDB info |
| 10 | Icecast Streams | `icecast` | Live mount status — listeners, uptime, stream URLs for all 9 zones |
| 11 | Feed Status | `feeds` | RSS/IPAWS feed health table |
| 12 | Zones | `zones` | Per-zone playlist config (normal mode types, P1 interrupt types) + audio queue file counts + zone definitions |
| 13 | Config | `config` | SMTP settings + service status indicators |

## What the Dashboard Shows

- Live NWS alert feed (reads `nws_alerts` MongoDB collection)
- **Weather Conditions tab** — current METAR obs for 16 FL cities via `airport_metar` collection
- **Traffic Alerts tab** — FL511 traffic incidents from `fl_traffic` collection (auto-refresh 2 min)
- **Zones tab** — `zone_pl_sel` selects zone; `normal_playlist_types` checkboxGroupInput saved to `zone_definitions.normal_mode_types` in MongoDB; Audio Queue table reads live file counts from `weather_station/audio/zones/<zone_id>/`
- **County Alerts tab** — dynamic per-county alert search with ZIP lookup, DataTable, PDF/email export
- Zone audio status (reads `zone_alert_wavs`)
- Icecast stream status
- Service health indicators

---

## Access

| URL | Port | Auth |
|-----|------|------|
| `https://128.227.67.234` | 443 → 3838 via Nginx (SSL) | None (public) |
| `http://128.227.67.234` | 80 → redirects to 443 | — |
| `http://128.227.67.234:3838` | 3838 | None (direct, bypasses SSL) |

Ports 80 and 443 are pending UF IT firewall approval — direct port 3838 access works now.
Self-signed cert at `/etc/ssl/certs/fpren.crt` (valid until Mar 2027); browsers will show
a security warning until replaced with a CA-signed cert (e.g. Let's Encrypt or UF InCommon).

---

## Shiny Server Config

Shiny Server is installed system-wide. Config: `/etc/shiny-server/shiny-server.conf`
App path: `/srv/shiny-server/fpren/`

```bash
# Service management
sudo systemctl status shiny-server
sudo systemctl restart shiny-server

# View Shiny logs
sudo journalctl -u shiny-server -f
tail -f /var/log/shiny-server/*.log
```

---

## R Dependencies

The app uses standard Shiny + tidyverse packages plus `mongolite` for MongoDB access. If adding new packages:

```r
# Install on the server (run once)
sudo Rscript -e "install.packages('package_name', repos='https://cran.rstudio.com/')"
```

**RStudio Server** is available at `http://128.227.67.234:8787` for interactive R development.

---

## Weather Conditions Tab Architecture

**tabName:** `wx_cities`

### Sub-features
1. **ZIP Code Forecast** — at top of tab; validates FL ZIP, resolves county via `FL_ZIP_RANGES`, looks up county centroid from `FL_COUNTY_LATLON`, calls NWS API for 7-day forecast, displays as horizontal scrollable day cards.
2. **16-City Card Grid** — METAR obs for 16 FL cities with radar thumbnails.
3. **Radar Thumbnails** — Iowa State public radar map (`mesonet.agron.iastate.edu/GIS/radmap.php`) centered on each city's lat/lon. Click to enlarge via Bootstrap modal. JS `setInterval` refreshes thumbnails every 5 min client-side via `&t=` cache-busting.

### Data Source
- MongoDB collection: `airport_metar`
- Queries 16 specific ICAO stations: `KJAX KTLH KGNV KOCF KMCO KDAB KTPA KSPG KSRQ KRSW KMIA KFLL KPBI KEYW KPNS KECP`
- Fetched fields: `icaoId`, `name`, `temp`, `dewp`, `wspd`, `wdir`, `visib`, `fltCat`, `obsTime`, `wxString`, `rhum`

### Display
- `uiOutput("wx_cities_grid")` renders a 4-column card grid via `renderUI`
- Each card color-coded by flight category: VFR=blue, MVFR=yellow, IFR=orange, LIFR=red
- Feels-like: wind chill (≤50°F) or heat index (≥80°F) computed inline in renderUI
- Humidity: from `rhum` field if present, otherwise derived from dewpoint via Magnus formula
- Auto-refreshes every 15 minutes via `reactiveTimer(900000)` + "Refresh Now" button

### ZIP Forecast Reactives
- `wx_zip_error_rv` — `reactiveVal("")` for error/progress messages
- `wx_zip_forecast_rv` — `reactiveVal(NULL)` stores list(location, county, periods, metar)
- `observeEvent(input$btn_wx_forecast)` — validates, resolves county, calls `nws_get_forecast()`
- `nws_get_forecast(lat, lon)` — calls `api.weather.gov/points` then `/forecast`; returns NULL on error
- `FL_COUNTY_LATLON` data.frame maps all 67 FL counties to approximate centroids

### Radar
- **Florida state radar:** `output$fl_state_radar_img` renders one NWS WMS composite for the
  full state (BBOX 24.5,-87.5 → 31.0,-80.0, 700×600 px). Placed in a collapsible box on the
  wx_cities tab above the city grid.
- **ZIP radar:** when a ZIP is looked up the nearest WX_CITIES city is found via Euclidean
  distance on lat/lon; `nws_radar_url(lat, lon)` builds a 420×420 WMS image centred ±1.5° on
  that city. Rendered inside the ZIP forecast panel (column 4).
- **NWS WMS endpoint:** `opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows` —
  EPSG:4326, WMS 1.3.0, BBOX order minLat,minLon,maxLat,maxLon. Returns 200 as of 2026-04-01.
- NWS `/ridge/lite/` and Iowa State `mesonet.agron.iastate.edu` are NOT used (Iowa State removed,
  RIDGE/lite returns 404).
- JS `setInterval` (300 000 ms) cache-busts `fl-state-radar` and `zip-city-radar` img ids.

### City → ICAO Mapping
`WX_CITIES` data.frame defined in server section maps 16 cities to their nearest ASOS stations, including lat/lon for radar centering.

---

## Traffic Alerts Tab Architecture

**tabName:** `traffic_alerts`

### Data Source
- MongoDB collection: `fl_traffic`
- Populated by `weather_rss/extended_fetcher.py` from FL511 API
- Key fields: `county`, `road`, `direction`, `type`, `severity`, `description`, `is_full_closure`, `last_updated`

### Inputs
- `traffic_county` — selectInput populated dynamically from distinct counties in data
- `traffic_severity` — Major / Minor / All
- `traffic_type` — incident type (Construction Zones, Accidents, etc.), populated from data
- `btn_traffic_refresh` — manual refresh trigger

### Reactives
- `traffic_data` — full collection query, invalidates every 2 min via `reactiveTimer(120000)`
- `traffic_filtered` — applies county/severity/type filters to `traffic_data()`

### Value Boxes
- `box_traffic_total` — total document count
- `box_traffic_major` — count where severity == "Major"
- `box_traffic_closures` — count where `is_full_closure == TRUE`
- `box_traffic_counties` — distinct county count

---

## County Alerts Tab Architecture

**tabName:** `county_alerts` (was `alachua`)

### Inputs
- `ca_zip` — 5-digit Florida ZIP code text input; auto-selects matching county in dropdown
- `ca_county` — `selectInput` of all 67 FL counties; shows representative ZIP hint
- `btn_ca_search` — triggers validation + MongoDB query

### Validation (ZIP)
`zip_to_florida_county()` helper at top of `app.R`:
1. Must be exactly 5 digits (`^\\d{5}$`)
2. Must be in FL range `32004–34997`
3. Matched against `FL_ZIP_RANGES` data.frame (range-based, ~200 rows covering all 67 counties)
4. Returns `NA` on no match → red error box displayed via `ca_error_ui`

### Data Flow
- `county_alerts_data` reactive queries `nws_alerts` with:
  ```
  {"$or":[{"area_desc":{"$regex":"<county>","$options":"i"}},
          {"source":"county_nws:<slug>"}]}
  ```
- `county_wavs_data` reactive queries `zone_alert_wavs` for this county's zone + `all_florida`
- Both use `invalidateLater(60000)` for 60-second auto-refresh
- `COUNTY_TO_ZONE` named vector maps all 67 counties to their Icecast zone

### DataTable
- `tbl_ca_alerts` — single-row selection; severity colors: Extreme=red, Severe=orange, Moderate=yellow, Minor=light
- `ca_alert_description` — verbatim text of clicked row's `description` field

### PDF Report
- Template: `reports/county_alerts_report.Rmd`
- Output dir: `reports/output/county_alerts_<county>_<timestamp>.pdf`
- Rendered via `rmarkdown::render()` with params: `county_name`, `date`, `mongo_uri`
- Contains: summary table, sortable alerts table, full descriptions, severity breakdown

### Email Report
- Reads `weather_rss/config/smtp_config.json` for SMTP settings
- Sends most-recent county PDF as attachment via `emayili`
- Subject: `FPREN County Alert Report - {County} - {Date}`

---

## Weather Trends Report Architecture

**Template:** `reports/weather_trends_report.Rmd`
**Params:** `icao`, `city_name`, `start_date`, `end_date`, `mongo_uri`
**Data source:** `weather_history` MongoDB collection (written hourly by `fpren-weather-history.timer`)

**Charts generated:**
1. Temperature trend line (actual + feels-like)
2. Wind speed trend with flight-category background shading
3. Humidity trend with 80% threshold reference line
4. Flight category pie/distribution chart
5. IFR/LIFR events table

**Reports tab UI:** `wt_city` selectInput (16 cities), `wt_dates` dateRangeInput (max 90 days),
`btn_gen_wx_trend` generates PDF, `btn_ca_email` / `wt_email` checkbox emails via SMTP.

---

## Weather History Collection

**MongoDB collection:** `weather_history` (database: `weather_rss`)
**Written by:** `scripts/store_weather_history.R` via `fpren-weather-history.timer`
**Frequency:** Hourly (on the hour)
**Retention:** 90 days (auto-purged)
**Compression:** Skips write if temp change < 2°F, wind < 5 kt, vis < 1 mi AND last record < 2h ago

**Document schema:**
```json
{
  "icao": "KGNV",
  "city": "Gainesville",
  "timestamp": "2026-04-01T15:00:00Z",
  "temp_f": 72.5,
  "feels_like_f": 70.0,
  "humidity": 65.0,
  "wind_speed": 8.0,
  "wind_dir": 270.0,
  "visibility": 10.0,
  "flight_cat": "VFR",
  "wx_desc": "Clear"
}
```

**Index:** `{icao: 1, timestamp: -1}`

---

## Business Continuity Plans (added 2026-04-03)

### Reports Tab — BCP Section
New section below Weather Trends reports:
- `bcp_username` selectInput — admin picks a user (populated from MongoDB `users`)
- `bcp_asset_id` selectInput — populated by asset list when user selected
- `btn_gen_bcp` — renders `reports/business_continuity_report.Rmd` via `rmarkdown::render`
- `bcp_email` checkbox — optionally emails PDF to the user's registered email
- `tbl_bcp_reports` — DT table listing `bcp_*.pdf` files in `reports/output/`

### Config Tab — User Assets Panel
New conditionalPanel (admin only) below User Management:
- `asset_mgmt_user` selectInput — pick a user to manage
- `user_assets_table` DT — shows that user's assets (asset_name, address, city, lat, lon, icao, etc.)
- `btn_delete_asset` — removes selected row's asset from MongoDB `$pull`
- Add Asset form: name, address, type, zip, lat, lon with ZIP lookup button
- `btn_lookup_zip` — calls `http://localhost:5000/api/lookup/city-by-zip?zip=<zip>`, fills city/airport dropdowns
- `btn_add_asset` — `$push` new asset into MongoDB

### Assets MongoDB Schema
Assets stored as `assets: []` array inside each `users` document:
```json
{ "asset_id": "16-char-id", "asset_name": "...", "address": "...",
  "lat": 29.65, "lon": -82.33, "zip": "32608", "city": "Gainesville",
  "nearest_airport_icao": "KGNV", "nearest_airport_name": "Gainesville Regional",
  "asset_type": "Radio Station", "notes": "", "created_at": "ISO8601" }
```

## Weather Cards 7-Day Hover (added 2026-04-03)
Each `.wx-card` div now has `data-lat`, `data-lon`, `data-icao` attributes.
JS (inline in `wx_cities_grid` renderUI) attaches `mouseenter/leave` handlers:
- 300ms delay before showing popup
- Calls `api.weather.gov/points/{lat},{lon}` then `/forecast`
- Renders up to 7 daytime periods as small colored tiles
- Caches responses in `_fcCache` keyed by `lat,lon`
- Popup flips left/right based on screen edge detection

## Playlist Priority Ordering (added 2026-04-03)
The Normal Mode Playlist in the Zones tab now uses a drag-to-reorder list (SortableJS CDN):
- Each row: grip handle + checkbox + label
- Drag reorders items; checkbox includes/excludes from playlist
- JS fires `Shiny.setInputValue('playlist_order', ...)` and `Shiny.setInputValue('normal_playlist_types', ...)`
- `btn_save_playlist_config` saves priority-ordered types to `zone_definitions.normal_mode_types`

## Gotchas

- Always deploy after editing — Shiny Server reads from `/srv/shiny-server/fpren/`, not from the repo.
- The `shiny` user must own the deployed file — always `chown shiny:shiny` after copy.
- MongoDB connection in `app.R` uses `mongolite` — it connects to `mongodb://localhost:27017/weather_rss`.
- If the dashboard shows a grey screen, check Shiny logs: `sudo journalctl -u shiny-server -f`.
- The `emayili` package must be installed: `sudo Rscript -e "install.packages('emayili')"`.
- `county_alerts_report.Rmd` requires `kableExtra` — install if missing.
- ZIP lookup is range-based (not USPS-authoritative) — covers all 67 FL counties with primary ranges.

---

## SNMP / System Status Panel (added 2026-04-05)

**Location:** Alerts tab (`tabName="alerts"`) — second fluidRow below the NWS alerts table.

Four valueBoxes + two DT tables reading from MongoDB `fpren_snmp_status` collection:
- `snmp_box_health` — systemHealth (OK/DEGRADED/CRITICAL)
- `snmp_box_services` — active service count / 11
- `snmp_box_alerts` — active NWS alert count
- `snmp_box_wx_cat` — worst flight category
- `tbl_snmp_services` — FPREN service OID table (name, status, OID)
- `tbl_snmp_asset_oids` — user asset OID map

Data written by `scripts/fpren_snmp_update.py` via `systemd/fpren-snmp-updater.timer` every 60s.
SNMP agent: `scripts/run_fpren_snmp.sh` → `scripts/fpren_snmp_agent.py` (pass_persist, OID base `1.3.6.1.4.1.64533`).
Community string: `fpren_monitor`. Test: `snmpwalk -v2c -c fpren_monitor localhost .1.3.6.1.4.1.64533.1`

---

## Emergency SMS Notifications (added 2026-04-05)

**Location:** Config tab — below User Assets panel.

### MongoDB:
- `emergency_roles_config` — per-(role, phase) to-do lists. `_id = "role|phase"`. 12 seed docs for Broadcast Engineer, County Emergency Manager, IT/Systems Administrator, Police Chief.
- `users.sms_emergency_enabled` (boolean, default TRUE) — opt-in flag.

### UI panels:
1. **SMS & Role Management** (in User Management box): inline-editable DT for role and SMS opt-in per user. Save via `btn_save_sms_roles`.
2. **Role-Based Action Checklists**: textarea editor per (role, phase). Load/save to `emergency_roles_config`.
3. **Emergency SMS Blast**: select target role + phase, preview SMS, send via Twilio (`btn_send_sms_blast`).

### SMS dispatch:
`weather_rss/emergency_sms.py --phones <csv> --role <role> --phase <phase> --mongo-uri <uri>`
Reads todos from MongoDB, formats as numbered list, sends via Twilio (credentials in `stream_notify_config.json`).

---

## BCP Report Enhancements (added 2026-04-05)

### New `profession` param
`reports/business_continuity_report.Rmd` now accepts `profession` param. Passed from `app.R` `.render_one_bcp()`.

### New sections in BCP:
1. **Waze Accident Hotspots** — queries `waze_alerts` (ACCIDENT type) and `waze_jams` (level≥3) within 15 km of asset using Haversine distance (last 6 hours).
2. **County Emergency Management** table — 9 major FL county EM offices with addresses + phones.
3. **State & Federal Agencies** table — FL DEM, FDLE, FDOT D2, FL National Guard, FEMA R4, FBI Tampa, Red Cross FL.
4. **Role-Specific Contacts** — per-profession agency contacts for Broadcast, Law Enforcement, EM, IT, Facility roles.
5. **SMS Emergency Action Checklist** — queries `emergency_roles_config` for the user's profession, renders 3-column (before/during/after) checklist table.
