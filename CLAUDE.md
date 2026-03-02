# CLAUDE.md

## Project Overview

This is a **Weather Station monitoring system** for Gainesville, FL and surrounding areas (Ocala, Palatka). It fetches NOAA/NWS RSS feeds, stores data in MongoDB, and provides multiple interfaces for monitoring and alerting.

## Repository Structure

```
/home/lh_admin/
├── weather_rss/          # Core weather RSS service
│   ├── weather_rss.py    # RSS feed fetcher (KGNV, KOCF, KPAK)
│   ├── weather_service.py# Main service (fetches + stores to MongoDB)
│   ├── alert_worker.py   # Processes weather alerts
│   ├── alert_player.py   # Audio playback for alerts
│   ├── check_feeds.py    # Feed health checks
│   ├── email_utils.py    # Email notifications
│   ├── weather_gui.py    # Tkinter GUI monitor
│   ├── weather_rss_gui.py# RSS GUI monitor
│   ├── weather_health.py # Service health monitoring
│   ├── weather_rss_dashboard_alert.py # Dashboard alert integration
│   ├── web/              # Flask web dashboard
│   ├── weather_web/      # Additional web assets
│   ├── grafana/          # Grafana dashboard configs
│   ├── config/           # Configuration files
│   ├── logs/             # Log output
│   ├── feeds/            # Downloaded RSS/XML feed files
│   ├── docker-compose.yml
│   └── Dockerfile
├── mongo_tts/            # MongoDB TTS monitor app
│   ├── app.py            # Flask app (port 5000)
│   ├── db.py             # MongoDB interface
│   ├── tts.py            # Text-to-speech
│   ├── importer.py       # JSON/XML importer
│   └── config.py
├── scripts/              # Utility scripts
├── chatgpt_cli.py        # ChatGPT CLI tool
└── FM Transmitter Air Chain.py  # FM transmitter integration
```

## Key Services (Docker Compose)

| Service         | Container            | Port  | Description                     |
|-----------------|----------------------|-------|---------------------------------|
| mongodb         | weather_mongo        | 27017 | MongoDB data store              |
| rss_fetcher     | weather_rss_fetcher  | -     | Fetches NOAA RSS feeds          |
| alert_worker    | weather_alerts       | -     | Processes alert rules           |
| web_dashboard   | weather_web          | 5000  | Flask web UI                    |
| grafana         | weather_grafana      | 3000  | Grafana visualization           |

## RSS Feed Sources

- `KGNV` — Gainesville, FL
- `KOCF` — Ocala, FL
- `KPAK` — Palatka, FL

Feeds fetched every **30 minutes**; XML files kept for **7 days**.

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

## Development Notes

- Logs go to `weather_rss/logs/` and are also written to service-specific `.log` files.
- The `venv_rss/` virtualenv is used for local development of the RSS service.
- `mongo_tts/venv/` is used for the TTS app.
- Feed XML files are stored under `weather_rss/feeds/`.
