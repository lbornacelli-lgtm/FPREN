import logging
import sys
from core.station_engine import StationEngine
from config.settings import Settings

def main():
    settings = Settings()

    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    logger = logging.getLogger("Main")
    logger.info("Starting Weather Station...")

    station = StationEngine(settings)
    station.start_station()

if __name__ == "__main__":
    main()
