weather_station/
├── main.py                     # Entry point for the station
├── requirements.txt            # Python dependencies
├── README.md
├── config/
│   └── settings.py             # Station settings & folder paths
├── core/
│   ├── main.py
│   ├── station_engine.py       # Orchestrates the station
│   ├── playlist_engine.py
│   ├── audio_engine.py
│   ├── tts_engine.py
│   ├── alert_process.py
│   ├── alert_watcher.py
│   ├── cleanup_manager.py
│   ├── fm_engine.py
│   ├── interrupt_engine.py
│   ├── scheduler.py
│   ├── station_manager.py
│   └── weather_processor.py    # Weather XML parser + WAV generator
├── services/
│   ├── __init__.py
│   ├── mongo_service.py        # MongoDB access
│   ├── watchdog.py             # Watchdog heartbeat
│   ├── fm_transmitter.py       # FM automation
│   ├── file_router.py          # Routes WAV files to correct folders
│   ├── tts_engine.py           # Text-to-speech generator
│   └── xml_parser.py           # XML parsing utility
├── audio/
│   ├── alerts/
│   │   ├── fire/
│   │   ├── freeze/
│   │   ├── flooding/
│   │   ├── tornado/
│   │   ├── thunderstorm/
│   │   └── other_alerts/
│   ├── educational/
│   │   ├── general/
│   │   └── history/
│   ├── imaging/
│   │   ├── jingles/
│   │   ├── sweepers/
│   │   └── station_ids/
│   ├── top_of_the_hour/
│   ├── weather/
│   │   ├── current_conditions/
│   │   └── weekly_forecast/
│   └── traffic/

