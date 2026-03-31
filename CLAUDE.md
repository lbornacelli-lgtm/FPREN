# CLAUDE.md — FPREN Weather Station

## Project Overview

FPREN (Florida Public Radio Emergency Network) is a weather radio broadcast system serving Florida. It fetches NWS/IPAWS alerts, converts them to audio via TTS, manages a broadcast playlist per zone, and streams audio over Icecast. A Shiny dashboard provides monitoring and control.

**Server:** `128.227.67.234` (UF VM)
**Primary dashboard:** `http://128.227.67.234` (Shiny, port 3838 via Nginx)
**Flask admin dashboard:** `http://128.227.67.234:5000` (requires login)
**Icecast stream:** `http://128.227.67.234:8000/fpren`

---

## Active Services (systemd)

| Service | File | Purpose |
|---------|------|---------|
| `beacon-web-dashboard` | `weather_rss/web/app.py` | Flask admin dashboard (port 5000) |
| `beacon-station-engine` | `weather_station/main.py` | TTS + Icecast broadcast engine |
| `beacon-ipaws-fetcher` | `weather_rss/ipaws_fetcher.py` | NWS/IPAWS alert fetcher |
| `beacon-obs-fetcher` | `weather_rss/weather_rss.py` | 19 FL ASOS station obs fetcher |
| `beacon-extended-fetcher` | `weather_rss/extended_fetcher.py` | Extended forecast + traffic data |
| `beacon-mongo-tts` | `mongo_tts/app.py` | MongoDB TTS monitor |
| `zone-alert-tts` | `weather_station/services/zone_alert_tts.py` | Alert → MP3 per zone |
| `icecast2` | system | Audio streaming server |
| `shiny-server` | `/srv/shiny-server/fpren/app.R` | Primary dashboard |
| `mongod` | system | MongoDB database |
| `nginx` | `/etc/nginx/sites-available/fpren` | Reverse proxy (port 80/443) |

---

## Repository Structure

```
Fpren-main/
├── weather_rss/                    # Alert/data fetchers (ACTIVE)
│   ├── ipaws_fetcher.py            # NWS/IPAWS FL alerts → MongoDB (runs every 2min)
│   ├── weather_rss.py              # 19 FL ASOS obs fetcher (runs every 15min)
│   ├── extended_fetcher.py         # Extended forecasts + FL511 traffic
│   ├── airport_delays_fetcher.py   # FAA airport delays
│   ├── alert_worker.py             # Alert rule processor
│   ├── web/
│   │   └── app.py                  # Flask admin dashboard (port 5000) — PRIMARY FLASK APP
│   └── config/
│       └── smtp_config.json        # Email settings
│
├── weather_station/                # Broadcast engine (ACTIVE)
│   ├── main.py                     # Entry point for beacon-station-engine
│   ├── core/
│   │   ├── tts_service.py          # Piper TTS (primary), falls back to gTTS
│   │   ├── audio_engine.py         # Audio playback + Icecast feed
│   │   ├── playlist_engine.py      # Hourly playlist builder
│   │   └── station_engine.py       # Main station loop
│   ├── services/
│   │   ├── zone_alert_tts.py       # Alert → MP3 per zone (MAIN PIPELINE)
│   │   ├── county_rss_fetcher.py   # NWS alerts by FL county FIPS code
│   │   ├── ai_classifier.py        # LiteLLM severity classification + text rewrite
│   │   ├── ai_client.py            # UF LiteLLM client (llama-3.3-70b-instruct)
│   │   ├── ai_playlist.py          # AI-driven playlist decisions
│   │   ├── broadcast_generator.py  # AI broadcast script → TTS audio
│   │   ├── elevenlabs_tts.py       # ElevenLabs TTS for critical alerts
│   │   ├── icecast_streamer.py     # FFmpeg → Icecast stream feeder
│   │   └── daily_report.py         # Daily alert report emailer
│   ├── config/
│   │   ├── settings.py             # All config (Icecast, paths, zone streams)
│   │   └── .env                    # API keys (not in git)
│   ├── voices/
│   │   └── en_US-amy-medium.onnx   # Piper TTS voice model
│   └── audio/
│       ├── content/                # Shared content library (uploaded via dashboard)
│       │   ├── top_of_hour/
│       │   ├── imaging/
│       │   ├── music/
│       │   ├── educational/
│       │   └── weather_report/
│       └── zones/                  # Generated alert audio per zone
│           ├── all_florida/
│           ├── north_florida/
│           ├── central_florida/
│           ├── south_florida/
│           ├── tampa/
│           ├── miami/
│           ├── orlando/
│           ├── jacksonville/
│           └── gainesville/
│
├── shiny_dashboard/
│   └── app.R                       # Shiny dashboard source (deploy to /srv/shiny-server/fpren/)
│
├── mongo_tts/                      # MongoDB TTS monitor (ACTIVE - beacon-mongo-tts)
│   └── app.py                      # Flask app
│
├── scripts/
│   ├── seed_zone_definitions.py    # Seeds 9 FL zones into MongoDB
│   ├── fetch_weather_rss.py        # Manual RSS fetch utility
│   ├── stream_monitor.py           # Stream health monitor
│   └── network_monitor.py          # Network interface monitor
│
├── systemd/                        # Systemd service file templates
├── reports/                        # R report generation scripts
├── archive/                        # Archived/deprecated files
├── logs/                           # Log files
└── CLAUDE.md                       # This file
```

---

## MongoDB Collections (database: `weather_rss`)

| Collection | Purpose |
|------------|---------|
| `nws_alerts` | All active NWS/IPAWS/county alerts |
| `zone_alert_wavs` | Tracking record for generated audio files |
| `zone_definitions` | 9 zone configs with county lists + cleanup rules |
| `fl_traffic` | FL511 traffic incidents |
| `users` | Dashboard user accounts (bcrypt hashed passwords) |
| `feed_status` | RSS feed health status |

---

## Zone Definitions (9 zones)

| Zone ID | Coverage | Cleanup |
|---------|----------|---------|
| `all_florida` | Catch-all (filtered: thunderstorm/hurricane/flood only) | 24h / max 10 files |
| `north_florida` | Counties north of Marion | 72h |
| `central_florida` | Marion → Palm Beach (Tampa, Daytona) | 72h |
| `south_florida` | Palm Beach south | 72h |
| `tampa` | Hillsborough + Pinellas | 72h |
| `miami` | Miami-Dade + Broward | 72h |
| `orlando` | Orange + Osceola + Seminole | 72h |
| `jacksonville` | Duval + Clay + St. Johns | 72h |
| `gainesville` | Alachua | 72h |

---

## TTS Stack

- **Piper** (primary) — local, free, no rate limits. Voice: `en_US-amy-medium`
- **ElevenLabs** — critical alerts only (tornado warning, hurricane warning, etc.)
- **gTTS** — removed as primary, no longer used

---

## AI Integration (UF LiteLLM)

- **Endpoint:** `https://api.ai.it.ufl.edu`
- **Model:** `llama-3.3-70b-instruct`
- **Key env var:** `UF_LITELLM_API_KEY` (in `weather_station/.env`)
- **Uses:** alert severity classification, broadcast script generation, playlist decisions

---

## Common Commands

```bash
# Activate virtualenv
cd ~/Fpren-main && source venv/bin/activate

# Service management
sudo systemctl status zone-alert-tts.service
sudo systemctl restart beacon-web-dashboard.service
sudo systemctl restart shiny-server

# Deploy Shiny dashboard changes
sudo cp shiny_dashboard/app.R /srv/shiny-server/fpren/app.R
sudo chown shiny:shiny /srv/shiny-server/fpren/app.R
sudo systemctl restart shiny-server

# Check Icecast stream
curl -s http://localhost:8000/status-json.xsl | python3 -m json.tool

# MongoDB quick checks
mongosh weather_rss --eval "print('Alerts:', db.nws_alerts.countDocuments())"
mongosh weather_rss --eval "print('Zones:', db.zone_definitions.countDocuments())"
mongosh weather_rss --eval "print('Audio records:', db.zone_alert_wavs.countDocuments())"

# Seed zones (run once or after reset)
python3 scripts/seed_zone_definitions.py

# Test Piper TTS
python3 -c "from weather_station.core.tts_service import TTSService; TTSService().say('Test', output_file='/tmp/t.mp3')"

# Check running services
sudo systemctl list-units | grep -E "fpren|beacon|zone|icecast|shiny|mongo"

# View logs
sudo journalctl -u zone-alert-tts.service -f
sudo journalctl -u beacon-station-engine.service -f
sudo tail -f weather_rss/logs/web_dashboard.log
```

---

## Environment Variables (weather_station/.env)

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

## Ports

| Port | Service |
|------|---------|
| 80 | Nginx (pending UF IT approval) |
| 443 | Nginx HTTPS (pending UF IT approval) |
| 3838 | Shiny Server (primary dashboard) |
| 5000 | Flask admin dashboard |
| 8000 | Icecast `/fpren` — All Florida stream |
| 8787 | RStudio Server |
| 27017 | MongoDB (internal only) |

---

## Known Issues / TODO

- [ ] Port 80/443 pending UF IT firewall approval
- [ ] Ports 8001-8010 pending UF IT approval (multi-zone streams)
- [ ] Wire `ai_classifier` into `zone_alert_tts.py`
- [ ] Multi-zone Icecast streaming (one FFmpeg per zone)
- [ ] Systemd unit files for `broadcast_generator`
- [ ] Fix Flask dashboard tab loading issue
- [ ] Desktop app (`weather_rss/web/fpren_desktop.py`) tab sync

---

## Git Workflow

```bash
git add -A
git commit -m "Description of change"
git push origin main
```

Repo: `https://github.com/lbornacelli-lgtm/FPREN.git`
