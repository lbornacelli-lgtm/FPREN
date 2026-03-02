# core/main.py
import logging
import time
from config.settings import Settings
from core.station_engine import StationEngine
from core.playlist_engine import PlaylistEngine
from services.watchdog import update_heartbeat
from services.fm_transmitter import FMTransmitter

def main():
    # Initialize settings
    settings = Settings()

    # Setup logging
    logging.basicConfig(
        filename=f"{settings.log_folder}/station.log",
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    logging.info("Weather station starting...")

    # Start heartbeat watchdog
    try:
        update_heartbeat()
        logging.info("Watchdog heartbeat updated successfully.")
    except PermissionError:
        logging.warning("Watchdog write failed: Permission denied.")

    # Initialize FM transmitter if enabled
    if settings.fm_enabled:
        fm = FMTransmitter(device=settings.fm_device, frequency=settings.fm_frequency)
        fm.start()
        logging.info(f"FM transmitter started at {settings.fm_frequency} MHz")

    # Initialize core station engine
    station = StationEngine(settings)

    # Initialize playlist engine
    playlist = PlaylistEngine()

    try:
        while True:
            # Update heartbeat every loop
            try:
                update_heartbeat()
            except Exception as e:
                logging.warning(f"Watchdog update failed: {e}")

            # Run the hourly playlist rotation
            playlist.run_hour()

    except KeyboardInterrupt:
        logging.info("Shutting down weather station...")
        if settings.fm_enabled:
            fm.stop()
        logging.info("Station stopped cleanly.")

if __name__ == "__main__":
    main()
