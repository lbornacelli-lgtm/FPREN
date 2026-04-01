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

## What the Dashboard Shows

- Live NWS alert feed (reads `nws_alerts` MongoDB collection)
- **County Alerts tab** — dynamic per-county alert search with ZIP lookup, DataTable, PDF/email export (replaces static Alachua tab)
- Current obs for 19 FL ASOS stations (reads `feed_status`)
- Zone audio status (reads `zone_alert_wavs`)
- Icecast stream status
- Service health indicators
- FL traffic incidents (reads `fl_traffic`)

---

## Access

| URL | Port | Auth |
|-----|------|------|
| `http://128.227.67.234` | 80 → 3838 via Nginx | None (public) |
| `http://128.227.67.234:3838` | 3838 | None (direct) |

Port 80 is pending UF IT firewall approval — direct port 3838 access works now.

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

## Gotchas

- Always deploy after editing — Shiny Server reads from `/srv/shiny-server/fpren/`, not from the repo.
- The `shiny` user must own the deployed file — always `chown shiny:shiny` after copy.
- MongoDB connection in `app.R` uses `mongolite` — it connects to `mongodb://localhost:27017/weather_rss`.
- If the dashboard shows a grey screen, check Shiny logs: `sudo journalctl -u shiny-server -f`.
- The `emayili` package must be installed: `sudo Rscript -e "install.packages('emayili')"`.
- `county_alerts_report.Rmd` requires `kableExtra` — install if missing.
- ZIP lookup is range-based (not USPS-authoritative) — covers all 67 FL counties with primary ranges.
