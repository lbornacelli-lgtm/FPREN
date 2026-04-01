import os
from dotenv import load_dotenv

load_dotenv()  # load .env variables

class Settings:
    WATCHDOG_PATH = os.getenv("WATCHDOG_PATH", "/tmp/weather_station.watchdog")
    FETCH_INTERVAL_SECONDS = int(os.getenv("FETCH_INTERVAL_SECONDS", 60))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    AUDIO_PATH = os.getenv("AUDIO_PATH", "/home/ufuser/Fpren-main/weather_station/audio")

    # Icecast streaming
    ICECAST_HOST            = os.getenv("ICECAST_HOST", "localhost")
    ICECAST_PORT            = int(os.getenv("ICECAST_PORT", "8000"))
    ICECAST_MOUNT           = os.getenv("ICECAST_MOUNT", "/fpren")
    ICECAST_SOURCE_PASSWORD = os.getenv("ICECAST_SOURCE_PASSWORD", "fpren_source")
    ZONE_STREAMS = [
        {'zone_id': 'all_florida',     'mount': '/fpren',           'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — All Florida'},
        {'zone_id': 'north_florida',   'mount': '/north-florida',   'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — North Florida'},
        {'zone_id': 'central_florida', 'mount': '/central-florida', 'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — Central Florida'},
        {'zone_id': 'south_florida',   'mount': '/south-florida',   'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — South Florida'},
        {'zone_id': 'miami',           'mount': '/miami',           'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — Miami'},
        {'zone_id': 'jacksonville',    'mount': '/jacksonville',    'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — Jacksonville'},
        {'zone_id': 'orlando',         'mount': '/orlando',         'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — Orlando'},
        {'zone_id': 'tampa',           'mount': '/tampa',           'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — Tampa'},
        {'zone_id': 'gainesville',     'mount': '/gainesville',     'port': 8000, 'name': 'FPREN Florida Public Radio Emergency Network — Gainesville'},
    ]
