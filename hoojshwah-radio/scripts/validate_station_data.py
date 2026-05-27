#!/usr/bin/env python3

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATION_PATH = ROOT / "data" / "station.json"
TRACKS_PATH = ROOT / "data" / "tracks.json"


def fail(message):
    print(f"ERROR: {message}")
    sys.exit(1)


def load_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        fail(f"Missing file: {path}")
    except json.JSONDecodeError as error:
        fail(f"Invalid JSON in {path}: {error}")


def main():
    station = load_json(STATION_PATH)
    tracks = load_json(TRACKS_PATH)

    media_base_url = station.get("media_base_url")
    if not media_base_url:
        fail("station.json is missing media_base_url")

    if not isinstance(tracks, list) or not tracks:
        fail("tracks.json must be a non-empty list")

    missing_audio_path = []
    hardcoded_audio_url = []
    local_static_refs = []
    missing_duration = []

    for index, track in enumerate(tracks):
        track_id = track.get("id", f"index-{index}")

        if not track.get("audio_path"):
            missing_audio_path.append(track_id)

        if "audio_url" in track:
            hardcoded_audio_url.append(track_id)

        if "/static/audio/" in json.dumps(track):
            local_static_refs.append(track_id)

        if not isinstance(track.get("duration_seconds"), int):
            missing_duration.append(track_id)

    if missing_audio_path:
        fail(f"Tracks missing audio_path: {missing_audio_path}")

    if hardcoded_audio_url:
        fail(f"tracks.json should not contain hardcoded audio_url entries: {hardcoded_audio_url}")

    if local_static_refs:
        fail(f"tracks.json still contains /static/audio references: {local_static_refs}")

    if missing_duration:
        fail(f"Tracks missing integer duration_seconds: {missing_duration}")

    unique_paths = sorted({track["audio_path"] for track in tracks})

    print("Station data validation passed.")
    print(f"Track entries: {len(tracks)}")
    print(f"Unique audio paths: {len(unique_paths)}")
    print(f"Media base URL: {media_base_url}")


if __name__ == "__main__":
    main()
