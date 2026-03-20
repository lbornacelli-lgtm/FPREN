# FPREN

A **24-hour Weather Station broadcast system** for Gainesville, FL and surrounding areas. Florida Public Radio Emergency Network ingests real-time NOAA/NWS weather data, stores it in MongoDB, converts it to speech via TTS, and transmits over an FM radio transmitter — providing continuous automated weather and emergency alert broadcasting.

> Research and development platform for automated emergency weather broadcasting.

---

## Features

- **Real-time FL weather alerts** — polls NWS statewide Florida alerts every 30 seconds, deduplicates by alert ID
- **19-station ASOS observation network** — fetches current conditions across Florida every 15 minutes
- **NHC tropical weather RSS** — Atlantic and East Pacific feeds polled every 5 minutes
- **Full audio broadcast chain** — TTS → audio processing → playlist engine → FM transmitter
- **Alert interrupts** — priority alerts preempt regular programming automatically
- **Web dashboard & Grafana** — real-time monitoring and visualization
- **Email & GUI notifications** — Tkinter monitor and email alerting

---

## System Architecture

```
NWS / NOAA / NHC
       │
       ▼
  RSS Fetcher (Docker)
       │
       ▼
   MongoDB
       │
       ▼
 Weather Station Engine
  ├── XML Parser
  ├── TTS Engine       → WAV files
  ├── Audio Chain      → normalized audio
  ├── Playlist Engine  → scheduled broadcast
  ├── Interrupt Engine → alert preemption
  └── FM Engine        → FM Transmitter
```

---

## Services

| Service | Container | Port | Description |
|---|---|---|---|
| mongodb | weather_mongo | internal | MongoDB data store |
| rss_fetcher | weather_rss_fetcher | — | NWS FL alerts (30s) + NHC RSS (5min) |
| obs_fetcher | weather_obs_fetcher | — | 19 FL ASOS stations (15min) |
| alert_worker | weather_alerts | — | Processes alert rules |
| web_dashboard | weather_web | 5000 | Flask web UI |
| grafana | weather_grafana | 3000 | Grafana visualization |

---

## Quick Start

```bash
# 1. Start Docker services (RSS, alerts, MongoDB, dashboard)
cd weather_rss
docker compose up -d

# 2. Start the broadcast station engine (TTS + FM)
cd weather_station
source venv/bin/activate
python main.py

# 3. MongoDB TTS monitor
cd mongo_tts
python app.py
```

---

## Stack

- **Language** — Python 3
- **Database** — MongoDB
- **Web** — Flask, Grafana
- **Containerization** — Docker / Docker Compose
- **Audio** — pydub, pyttsx3, sounddevice, pedalboard
- **Feed parsing** — feedparser, requests
- **GUI** — Tkinter

---

## Coverage

Florida ASOS stations monitored:
`KGNV` `KOCF` `KPAK` `KJAX` `KTLH` `KPNS` `KECP` `KMCO` `KDAB` `KTPA` `KSRQ` `KLAL` `KRSW` `KFLL` `KMIA` `KPBI` `KEYW` `KSPG` `KAPF`
# Last updated: Thu Mar 19 13:16:34 UTC 2026
