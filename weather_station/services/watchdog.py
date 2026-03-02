# ~/weather_station/services/watchdog.py

from datetime import datetime, timezone
import logging

def update_heartbeat(path: str):
    """
    Update the heartbeat watchdog file with current UTC timestamp
    """
    try:
        with open(path, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except Exception as e:
        logging.getLogger("Watchdog").error(f"Failed to update heartbeat: {e}")
