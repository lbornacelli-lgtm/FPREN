from config import STATIONS

class Station:
    def __init__(self, name, audio_path, mongo_filter):
        self.name = name
        self.audio_path = audio_path
        self.mongo_filter = mongo_filter

stations = []

def load_stations():
    for s in STATIONS:
        stations.append(
            Station(
                s["name"],
                s["audio_path"],
                s["mongo_filter"]
            )
        )
