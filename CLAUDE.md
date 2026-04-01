# CLAUDE.md — FPREN Master Context

FPREN (Florida Public Radio Emergency Network) is a 24/7 automated weather radio broadcast system serving Florida. It fetches NWS/IPAWS alerts, converts them to speech via TTS, manages a broadcast playlist per zone, and streams audio over Icecast. A Shiny dashboard provides real-time monitoring; a Flask app provides admin control.

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
| `zone-alert-tts` | `weather_station/services/zone_alert_tts.py` | Alert → MP3 per zone (main pipeline) |
| `fpren-broadcast-generator` | `weather_station/services/broadcast_generator.py` | AI broadcast scripts → MP3 per zone (every 30 min via timer) |
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
| `users` | Dashboard user accounts (bcrypt hashed) |
| `feed_status` | RSS feed health status |

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
| 8000 | Icecast `/fpren` (All Florida) | Active |
| 8001–8010 | Multi-zone Icecast streams | Pending UF IT approval |
| 8787 | RStudio Server | Active |
| 27017 | MongoDB | Internal only |

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
- **Multi-zone streams** (ports 8001–8010) are stubbed in `multi_zone_streamer.py` but blocked by UF IT firewall — do not try to start them.
- **venv** must be active for all Python commands. Located at `~/Fpren-main/venv/`.
- **Flask login** uses MongoDB `users` collection with bcrypt. No default admin — seed via `mongosh` if locked out.
- **Piper binary** must be on PATH. It is installed system-wide, not in venv.
- **ElevenLabs** is only triggered for tornado/hurricane warnings. Never use it for routine obs — it costs money.
- **`ai_classifier.py`** is implemented but not yet wired into `zone_alert_tts.py` — it is called separately.

---

## Known Issues / TODO

- [ ] Port 80/443 pending UF IT firewall approval
- [ ] Ports 8001–8010 pending UF IT approval (multi-zone streams)
- [ ] Wire `ai_classifier` into `zone_alert_tts.py` main pipeline
- [ ] Multi-zone Icecast streaming (one FFmpeg process per zone)
- [ ] Fix Flask dashboard tab loading issue
- [ ] Desktop app (`weather_rss/web/fpren_desktop.py`) tab sync

---

## Subdirectory CLAUDE.md Files

Each major subdirectory has its own CLAUDE.md with component-specific context:

- [`weather_rss/CLAUDE.md`](weather_rss/CLAUDE.md) — fetcher pipeline, data sources, MongoDB writes
- [`weather_station/CLAUDE.md`](weather_station/CLAUDE.md) — broadcast engine, TTS, zone audio, Icecast
- [`shiny_dashboard/CLAUDE.md`](shiny_dashboard/CLAUDE.md) — Shiny app, deploy workflow
- [`weather_rss/web/CLAUDE.md`](weather_rss/web/CLAUDE.md) — Flask admin, auth, all API routes

> **Rule:** Every new feature added to this project must include a corresponding update to the relevant subdirectory CLAUDE.md file.
