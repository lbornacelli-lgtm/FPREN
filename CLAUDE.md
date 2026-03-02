# CLAUDE.md

## Project Overview

This is a **Weather Station monitoring system** for Gainesville, FL and surrounding areas (Ocala, Palatka). It fetches NOAA/NWS RSS feeds, stores data in MongoDB, and provides multiple interfaces for monitoring and alerting.

## Repository Structure

```
/home/lh_admin/
├── weather_rss/                      # Core weather RSS service (Dockerized)
│   ├── weather_service.py            # NWS FL alerts (30s) + NHC RSS (5min) → MongoDB
│   ├── weather_rss.py                # 19 FL ASOS station obs fetcher (15min)
│   ├── alert_worker.py               # Processes alert rules from MongoDB
│   ├── alert_player.py               # Audio playback for alerts
│   ├── weather_rss_service.py        # Service wrapper
│   ├── weather_rss_dashboard.py      # Dashboard integration
│   ├── weather_rss_dashboard_alert.py# Alert dashboard integration
│   ├── weather_rss_gui.py            # RSS GUI monitor
│   ├── weather_gui.py                # Tkinter GUI monitor
│   ├── weather_health.py             # Service health monitoring
│   ├── check_feeds.py                # Feed health checks
│   ├── email_utils.py                # Email notifications
│   ├── weather_rss_email.sh          # Email shell script
│   ├── start_weather_gui.sh          # GUI launcher
│   ├── web/                          # Flask web dashboard
│   ├── static/                       # Static web assets
│   ├── audio/                        # Alert audio files
│   ├── grafana/                      # Grafana dashboard configs
│   ├── config/                       # Configuration files
│   ├── docs/                         # Documentation
│   ├── logs/                         # Log output
│   ├── feeds/                        # Downloaded RSS/XML feed files (7-day retention)
│   ├── requirements.txt
│   ├── docker-compose.yml
│   └── Dockerfile
├── weather_station/                  # Standalone weather station project
│   ├── main.py                       # Entry point
│   ├── audio_chain.py                # Audio processing chain
│   ├── core/                         # Core logic
│   ├── processing/                   # Data processing
│   ├── services/                     # Service modules
│   ├── stations/                     # Station definitions
│   ├── dashboard/                    # Dashboard UI
│   ├── fm/                           # FM transmitter integration
│   ├── audio/                        # Audio assets
│   ├── config/                       # Configuration
│   ├── data/                         # Data storage
│   └── requirements.txt
├── mongo_tts/                        # MongoDB TTS monitor app
│   ├── app.py                        # Flask app (port 5000)
│   ├── db.py                         # MongoDB interface
│   ├── tts.py                        # Text-to-speech engine
│   ├── importer.py                   # JSON/XML importer
│   ├── desktop.py                    # Desktop integration
│   ├── config.py                     # Configuration
│   ├── templates/                    # Jinja2 templates
│   ├── static/                       # Static web assets
│   └── requirements.txt
├── audio_playlist/                   # Organized audio library
│   ├── alerts/                       # Alert audio (tornado, fire, flood, freeze, etc.)
│   ├── weather/                      # Weather conditions + forecasts
│   ├── educational/                  # Educational content
│   ├── imaging/                      # Station IDs, jingles, sweepers
│   ├── traffic/                      # Traffic reports
│   ├── top_of_the_hour/              # Top-of-hour content
│   └── generated_wav_files/          # TTS-generated audio
├── wav_output/                       # TTS WAV output files
├── scripts/                          # Utility scripts
│   └── fetch_weather_rss.py
├── chatgpt_cli.py                    # ChatGPT CLI tool
└── FM Transmitter Air Chain.py       # FM transmitter air chain
```

## Key Services (Docker Compose)

| Service         | Container            | Port          | Description                                          |
|-----------------|----------------------|---------------|------------------------------------------------------|
| mongodb         | weather_mongo        | internal only | MongoDB data store (no host port — local mongod owns 27017) |
| rss_fetcher     | weather_rss_fetcher  | -             | NWS FL alerts every 30s + NHC RSS every 5min         |
| obs_fetcher     | weather_obs_fetcher  | -             | 19 FL ASOS station observations every 15min          |
| alert_worker    | weather_alerts       | -             | Processes alert rules                                |
| web_dashboard   | weather_web          | 5000          | Flask web UI                                         |
| grafana         | weather_grafana      | 3000          | Grafana visualization                                |

## Data Sources

**NWS Alerts** — `api.weather.gov/alerts/active?area=FL` (statewide FL, polled every 30s, deduped by alert ID)

**ASOS Current Observations** — 19 FL stations polled every 15min, deduped by `observation_time`:
`KGNV` `KOCF` `KPAK` `KJAX` `KTLH` `KPNS` `KECP` `KMCO` `KDAB` `KTPA` `KSRQ` `KLAL` `KRSW` `KFLL` `KMIA` `KPBI` `KEYW` `KSPG` `KAPF`

**NHC RSS** — Atlantic and East Pacific tropical weather feeds, polled every 5min

XML files kept for **7 days** under `weather_rss/feeds/`.

## Stack

- **Language**: Python 3
- **Database**: MongoDB (via `pymongo`)
- **Web**: Flask
- **Containerization**: Docker / Docker Compose
- **Visualization**: Grafana
- **GUI**: Tkinter
- **Feed parsing**: `feedparser`, `requests`

## Common Commands

```bash
# Start all services
cd /home/lh_admin/weather_rss && docker compose up -d

# View logs
docker logs weather_rss_fetcher -f
docker logs weather_alerts -f

# Run GUI monitor
cd /home/lh_admin/weather_rss && bash start_weather_gui.sh

# Run mongo_tts app
cd /home/lh_admin/mongo_tts && python app.py
```

## Environment Variables

- `MONGO_URI` — MongoDB connection string (default: `mongodb://mongodb:27017`)
- Set in `docker-compose.yml` for containerized services; set in `config.py` for local runs.

## Git Workflow

Default branch is `main` — branch protection is enabled, changes go via feature branches and PRs.

```bash
# Start a new feature
git checkout main && git pull
git checkout -b feature/your-feature-name

# Work, commit, push
git add <files>
git commit -m "Description of change"
git push origin feature/your-feature-name

# Open a PR
gh pr create --base main --title "..." --body "..."

# After merge, clean up
git checkout main && git pull
git branch -d feature/your-feature-name
```

Branch naming: `feature/`, `fix/`, `chore/` prefixes.

## Development Notes

- Logs go to `weather_rss/logs/` and are also written to service-specific `.log` files.
- The `venv_rss/` virtualenv is used for local development of the RSS service.
- `mongo_tts/venv/` is used for the TTS app.
- Feed XML files are stored under `weather_rss/feeds/`.
