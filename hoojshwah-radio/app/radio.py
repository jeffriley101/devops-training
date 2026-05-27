import copy
import json
from pathlib import Path


TRACKS_PATH = Path("data/tracks.json")
STATION_PATH = Path("data/station.json")
PLAYLISTS_DIR = Path("data/playlists")


def load_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_tracks():
    return load_json(TRACKS_PATH)


def load_station_config():
    return load_json(STATION_PATH)


def load_playlist(playlist_id):
    return load_json(PLAYLISTS_DIR / f"{playlist_id}.json")


def build_audio_url(media_base_url, audio_path):
    return f"{media_base_url.rstrip('/')}/{audio_path.lstrip('/')}"


def build_playlist_tracks(tracks, playlist, media_base_url):
    tracks_by_id = {track["id"]: track for track in tracks}
    station_tracks = []

    for track_id in playlist["track_ids"]:
        track = copy.deepcopy(tracks_by_id[track_id])

        if "audio_url" not in track and "audio_path" in track:
            track["audio_url"] = build_audio_url(media_base_url, track["audio_path"])

        station_tracks.append(track)

    return station_tracks


def build_station_tracks(tracks, media_base_url, playlist=None):
    if playlist is None:
        station_tracks = copy.deepcopy(tracks)

        for track in station_tracks:
            if "audio_url" not in track and "audio_path" in track:
                track["audio_url"] = build_audio_url(media_base_url, track["audio_path"])

        return station_tracks

    return build_playlist_tracks(
        tracks=tracks,
        playlist=playlist,
        media_base_url=media_base_url,
    )
