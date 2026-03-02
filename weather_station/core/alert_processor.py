import os
import re
import logging
from datetime import datetime

logger = logging.getLogger("AlertProcessor")


def _safe_filename(text: str) -> str:
    """Sanitize a string for use as a filename."""
    return re.sub(r"[^\w\-]", "_", text.strip())[:60]


def process_alerts(mongo_service, tts_engine, file_router):
    """
    Fetch unprocessed NWS alerts from MongoDB, convert each to a WAV file
    via TTS, save it to the correct audio_playlist/alerts/ subfolder, and
    mark the alert as processed so it is not re-converted on the next run.

    Folder routing:
        tornado warning/watch       → audio_playlist/alerts/tornado/
        tornado emergency           → audio_playlist/alerts/priority_1/
        severe thunderstorm *       → audio_playlist/alerts/thunderstorm/
        flash flood emergency       → audio_playlist/alerts/priority_1/
        flash flood / flood *       → audio_playlist/alerts/flooding/
        red flag / fire weather *   → audio_playlist/alerts/fire/
        freeze / frost / winter *   → audio_playlist/alerts/freeze/
        everything else             → audio_playlist/alerts/other_alerts/
    """
    alerts = mongo_service.fetch_unprocessed_alerts()

    if not alerts:
        logger.debug("No unprocessed alerts.")
        return

    logger.info(f"Processing {len(alerts)} unprocessed alert(s).")

    for alert in alerts:
        alert_id  = alert["alert_id"]
        event     = alert["event"]
        headline  = alert["headline"]
        desc      = alert["description"]
        area      = alert["area_desc"]

        # Build the spoken text
        text = f"{event}. {headline}."
        if area:
            text += f" Affected areas: {area}."
        if desc:
            # Trim description to avoid very long audio — first 500 chars
            short_desc = desc[:500].rsplit(".", 1)[0] + "."
            text += f" {short_desc}"

        # Determine output folder based on event type
        folder = file_router.route_alert_by_event(event)

        # Build a unique filename: EventType_AlertID_Timestamp.wav
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"{_safe_filename(event)}_{_safe_filename(alert_id[-12:])}_{timestamp}.wav"
        wav_path  = os.path.join(folder, filename)

        # Generate WAV via TTS
        result = tts_engine.say(text, output_file=wav_path)

        if result:
            logger.info(f"Generated: {wav_path}")
            mongo_service.mark_alert_tts_done(alert_id, wav_path)
        else:
            logger.error(f"TTS failed for alert_id={alert_id}")
