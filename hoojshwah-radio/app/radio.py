import json
from pathlib import Path


TRACKS_PATH = Path("data/tracks.json")


def load_tracks():
    with TRACKS_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)
