import os
from services.mongo_service import get_active_alerts
from services.xml_parser import parse_xml
from services.tts_engine import text_to_wav
from services.file_router import route_alert


def process_alerts():
    alerts = get_active_alerts()

    for alert in alerts:
        parsed = parse_xml(alert["xml"])
        folder = route_alert(parsed)

        filename = f"{parsed['event'].replace(' ','_')}.wav"
        output_path = os.path.join(folder, filename)

        text = f"{parsed['headline']}. {parsed['description']}"
        text_to_wav(text, output_path)
