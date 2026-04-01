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

## Tab Order and Descriptions

| # | Label | tabName | Description |
|---|-------|---------|-------------|
| 1 | Overview | `overview` | Value boxes + active alerts summary + station status |
| 2 | Weather Conditions | `wx_cities` | Card grid of current METAR obs for 16 FL cities |
| 3 | FL Alerts | `alerts` | All active NWS/IPAWS alerts with severity/source filters |
| 4 | Traffic Alerts | `traffic_alerts` | FL511 traffic incidents with county/severity/type filters |
| 5 | County Alerts | `county_alerts` | ZIP/county search for per-county alerts + PDF/email export |
| 6 | Airport Delays & Weather | `airports` | FAA delay status merged with METAR obs for FL airports |
| 7 | Upload Content | `upload` | Upload MP3/WAV to broadcast content folders |
| 8 | Reports | `reports` | Generate + email PDF alert summary reports |
| 9 | Station Health | `health` | Recent audio files + system/MongoDB info |
| 10 | Feed Status | `feeds` | RSS/IPAWS feed health table |
| 11 | Zones | `zones` | Zone definitions + user management |
| 12 | Config | `config` | SMTP settings + service status indicators |

## What the Dashboard Shows

- Live NWS alert feed (reads `nws_alerts` MongoDB collection)
- **Weather Conditions tab** — current METAR obs for 16 FL cities via `airport_metar` collection
- **Traffic Alerts tab** — FL511 traffic incidents from `fl_traffic` collection (auto-refresh 2 min)
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
- `WX_RADAR` named vector: city ICAO → nearest NWS radar station (informational)
- `wx_radar_url(lat, lon)` helper: builds Iowa State radar map URL for given coordinates
- NWS `/ridge/lite/` endpoint returns 404 as of 2026 — Iowa State service is used instead
- Radar modal: `id="radar-enlarge-modal"` Bootstrap modal + `showRadarModal()` JS function

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

## Gotchas

- Always deploy after editing — Shiny Server reads from `/srv/shiny-server/fpren/`, not from the repo.
- The `shiny` user must own the deployed file — always `chown shiny:shiny` after copy.
- MongoDB connection in `app.R` uses `mongolite` — it connects to `mongodb://localhost:27017/weather_rss`.
- If the dashboard shows a grey screen, check Shiny logs: `sudo journalctl -u shiny-server -f`.
- The `emayili` package must be installed: `sudo Rscript -e "install.packages('emayili')"`.
- `county_alerts_report.Rmd` requires `kableExtra` — install if missing.
- ZIP lookup is range-based (not USPS-authoritative) — covers all 67 FL counties with primary ranges.
