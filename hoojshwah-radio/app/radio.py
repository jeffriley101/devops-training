import copy
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


def build_audio_url(media_base_url, audio_path):
    return f"{media_base_url.rstrip('/')}/{audio_path.lstrip('/')}"


def build_station_tracks(tracks, media_base_url):
    station_tracks = copy.deepcopy(tracks)

    for track in station_tracks:
        if "audio_url" not in track and "audio_path" in track:
            track["audio_url"] = build_audio_url(media_base_url, track["audio_path"])

    return station_tracks
