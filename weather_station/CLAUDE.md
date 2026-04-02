# CLAUDE.md — weather_station (Broadcast Engine)

This directory is the core broadcast engine. It reads alerts from MongoDB, generates audio via TTS, builds zone playlists, and feeds audio to Icecast. See root [`CLAUDE.md`](../CLAUDE.md) for overall system context.

---

## Entry Point

```
main.py → core/station_engine.py → orchestrates everything below
```

Run by systemd service `beacon-station-engine`.

---

## Core Subsystems

### `core/` — Station Loop

| File | Role |
|------|------|
| `station_engine.py` | Main loop — schedules playlist, handles interrupts |
| `playlist_engine.py` | Hourly playlist builder from audio content library |
| `audio_engine.py` | Audio playback + Icecast feed management |
| `tts_service.py` | Piper TTS (primary) — wraps Piper binary + file output |
| `tts_engine.py` | Legacy TTS wrapper |
| `alert_processor.py` | Reads `nws_alerts`, determines what needs audio |
| `interrupt_engine.py` | Preempts regular playlist for priority alerts |
| `scheduler.py` | Cron-like scheduling within station loop |
| `cleanup_manager.py` | Prunes old audio files per zone cleanup rules |
| `fm_engine.py` | FM transmitter integration (hardware) |

### `services/` — Supporting Services

| File | Role |
|------|------|
| `zone_alert_tts.py` | **MAIN PIPELINE** — alert → MP3 per zone (run by `zone-alert-tts` service) |
| `ai_classifier.py` | LiteLLM severity classification + text rewrite |
| `ai_client.py` | UF LiteLLM API client (`llama-3.3-70b-instruct`) |
| `ai_playlist.py` | AI-driven playlist decisions |
| `broadcast_generator.py` | AI broadcast script → TTS audio (run by `fpren-broadcast-generator` service/timer) |
| `elevenlabs_tts.py` | ElevenLabs TTS for critical alerts only |
| `icecast_streamer.py` | FFmpeg → Icecast stream feeder |
| `county_rss_fetcher.py` | NWS alerts by FL county using NWS forecast zone codes (FLZ*) — tags alerts with `source:"county_nws:<county>"` for County Alerts tab |
| `daily_report.py` | Daily alert summary emailer |
| `multi_zone_streamer.py` | Multi-zone Icecast — one FFmpeg/FIFO/streamer per zone, all on port 8000 (run by `fpren-multi-zone-streamer` service; UF IT firewall blocks external access to zone mounts) |
| `mongo_service.py` | MongoDB connection + alert queries |
| `watchdog.py` | Heartbeat watchdog |
| `wav_cleanup.py` | Audio file cleanup worker |
| `playback_tracker.py` | Tracks what has been played per zone |

---

## TTS Stack

| Engine | Trigger | Details |
|--------|---------|---------|
| **Piper** | All regular alerts and obs | Local binary, no API cost. Voice: `en_US-amy-medium.onnx` at `voices/`. Invoked via subprocess. |
| **ElevenLabs** | Critical only: tornado warning, hurricane warning | API-based, costs money — only for high-impact events. Key in `.env`. |

**Piper voice model path:** `weather_station/voices/en_US-amy-medium.onnx`

To test Piper:
```bash
cd ~/Fpren-main && source venv/bin/activate
python3 -c "from weather_station.core.tts_service import TTSService; TTSService().say('Test broadcast', output_file='/tmp/test.mp3')"
```

---

## Zone Audio Pipeline

1. `zone_alert_tts.py` polls MongoDB `nws_alerts` for unprocessed alerts.
2. For each alert, determines which zone(s) it applies to (by county FIPS matching against `zone_definitions`).
3. Generates MP3 via Piper (or ElevenLabs for critical).
4. Writes MP3 to `audio/zones/<zone_id>/`.
5. Writes tracking record to `zone_alert_wavs` collection.
6. `station_engine.py` / `interrupt_engine.py` picks up new files from the zone audio directory.

**Zone audio directories:**
```
weather_station/audio/zones/
├── all_florida/
├── north_florida/
├── central_florida/
├── south_florida/
├── tampa/
├── miami/
├── orlando/
├── jacksonville/
└── gainesville/
```

---

## Content Library

Shared broadcast content (uploaded via Flask admin dashboard):
```
weather_station/audio/content/
├── top_of_hour/        # Top-of-hour IDs / news breaks
├── imaging/            # Jingles, sweepers, station IDs
├── music/              # Background / fill music
├── educational/        # Weather education segments
└── weather_report/     # Pre-recorded weather segments
```

---

## Configuration

`config/settings.py` — all station config: Icecast credentials, paths, zone stream definitions, audio dirs.
`config/.env` — API keys (not in git). See root CLAUDE.md for env var names.
`config/stream_zone_overrides.json` — runtime zone-to-stream override (edited via Flask admin).

---

## AI Integration

All AI calls go through `services/ai_client.py` → UF LiteLLM endpoint.

| Use case | File | Notes |
|----------|------|-------|
| Alert severity classification | `ai_classifier.py` | Wired into `zone_alert_tts.py` — called once per alert before the zone loop; falls back to Piper on failure |
| Broadcast script generation | `broadcast_generator.py` | Generates on-air copy from alert data |
| Playlist decisions | `ai_playlist.py` | Chooses content mix for hour |

---

## Icecast Streaming

`services/icecast_streamer.py` spawns FFmpeg to push audio to Icecast.

- **Default mount:** `/fpren` on port 8000 (All Florida stream)
- **Multi-zone** mounts all run on port 8000 via separate mount points (`/north-florida`, `/central-florida`, etc.) — managed by `fpren-multi-zone-streamer` service. UF IT firewall blocks external access to all zone mounts except `/fpren`.
- Icecast admin: `http://localhost:8000/admin/` (credentials in Icecast config)

```bash
# Check stream health
curl -s http://localhost:8000/status-json.xsl | python3 -m json.tool

# Check Icecast logs
sudo journalctl -u icecast2 -f
```

---

## Common Commands

```bash
# Service management
sudo systemctl status beacon-station-engine
sudo systemctl restart beacon-station-engine
sudo systemctl status zone-alert-tts
sudo systemctl restart zone-alert-tts

# View live broadcast log
sudo journalctl -u beacon-station-engine.service -f
sudo journalctl -u zone-alert-tts.service -f

# Check zone audio output
ls -lht weather_station/audio/zones/gainesville/
ls -lht weather_station/audio/zones/all_florida/

# Check what's been generated in MongoDB
mongosh weather_rss --eval "db.zone_alert_wavs.find().sort({created_at:-1}).limit(5).toArray()"

# Seed zone definitions (run once after DB reset)
python3 scripts/seed_zone_definitions.py
```

---

## Gotchas

- `zone-alert-tts` and `beacon-station-engine` are separate systemd services — restarting one does not affect the other.
- Audio files in `audio/zones/` are cleaned up by `cleanup_manager.py` per zone rules. Files older than cleanup threshold are auto-deleted.
- ElevenLabs is rate-limited and costs money — it is gated to critical alert event types in `elevenlabs_tts.py`.
- Piper must be installed system-wide (`which piper` to verify). It is not a Python package.
- `ai_classifier.py` is wired into `zone_alert_tts.py` — called once per alert before the zone loop, with Piper fallback on failure.
- The `fm_engine.py` is for hardware FM transmitter integration — not relevant for Icecast streaming path.
