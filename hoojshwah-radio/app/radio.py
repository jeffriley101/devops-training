import json
from pathlib import Path


TRACKS_PATH = Path("data/tracks.json")
STATION_PATH = Path("data/station.json")


def load_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_tracks():
    return load_json(TRACKS_PATH)


def load_station_config():
    return load_json(STATION_PATH)
