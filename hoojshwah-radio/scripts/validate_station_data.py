#!/usr/bin/env python3

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATION_PATH = ROOT / "data" / "station.json"
TRACKS_PATH = ROOT / "data" / "tracks.json"
PLAYLISTS_DIR = ROOT / "data" / "playlists"


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


def validate_tracks(tracks):
    if not isinstance(tracks, list) or not tracks:
        fail("tracks.json must be a non-empty list")

    seen_ids = set()
    duplicate_ids = []
    missing_id = []
    missing_audio_path = []
    hardcoded_audio_url = []
    local_static_refs = []
    missing_duration = []

    for index, track in enumerate(tracks):
        track_id = track.get("id")

        if not track_id:
            missing_id.append(f"index-{index}")
            track_id = f"index-{index}"

        if track_id in seen_ids:
            duplicate_ids.append(track_id)
        seen_ids.add(track_id)

        if not track.get("audio_path"):
            missing_audio_path.append(track_id)

        if "audio_url" in track:
            hardcoded_audio_url.append(track_id)

        if "/static/audio/" in json.dumps(track):
            local_static_refs.append(track_id)

        if not isinstance(track.get("duration_seconds"), int):
            missing_duration.append(track_id)

    if missing_id:
        fail(f"Tracks missing id: {missing_id}")

    if duplicate_ids:
        fail(f"Duplicate track ids: {duplicate_ids}")

    if missing_audio_path:
        fail(f"Tracks missing audio_path: {missing_audio_path}")

    if hardcoded_audio_url:
        fail(f"tracks.json should not contain hardcoded audio_url entries: {hardcoded_audio_url}")

    if local_static_refs:
        fail(f"tracks.json still contains /static/audio references: {local_static_refs}")

    if missing_duration:
        fail(f"Tracks missing integer duration_seconds: {missing_duration}")

    return {track["id"]: track for track in tracks}


def validate_playlists(station, track_by_id):
    active_playlist = station.get("active_playlist")
    if not active_playlist:
        fail("station.json is missing active_playlist")

    if not PLAYLISTS_DIR.exists():
        fail(f"Missing playlists directory: {PLAYLISTS_DIR}")

    playlist_files = sorted(PLAYLISTS_DIR.glob("*.json"))
    if not playlist_files:
        fail(f"No playlist files found in: {PLAYLISTS_DIR}")

    playlist_ids = set()
    total_playlist_entries = 0

    for playlist_path in playlist_files:
        playlist = load_json(playlist_path)
        playlist_id = playlist.get("id")
        track_ids = playlist.get("track_ids")

        if not playlist_id:
            fail(f"{playlist_path} is missing id")

        if playlist_id in playlist_ids:
            fail(f"Duplicate playlist id: {playlist_id}")

        playlist_ids.add(playlist_id)

        if not isinstance(track_ids, list) or not track_ids:
            fail(f"{playlist_path} must contain a non-empty track_ids list")

        total_playlist_entries += len(track_ids)

        missing_tracks = [track_id for track_id in track_ids if track_id not in track_by_id]
        if missing_tracks:
            fail(f"{playlist_path} references missing track ids: {missing_tracks}")

    if active_playlist not in playlist_ids:
        fail(f"active_playlist '{active_playlist}' does not match any playlist id")

    return playlist_files, total_playlist_entries


def main():
    station = load_json(STATION_PATH)
    tracks = load_json(TRACKS_PATH)

    media_base_url = station.get("media_base_url")
    if not media_base_url:
        fail("station.json is missing media_base_url")

    track_by_id = validate_tracks(tracks)
    playlist_files, total_playlist_entries = validate_playlists(station, track_by_id)

    unique_paths = sorted({track["audio_path"] for track in tracks})

    print("Station data validation passed.")
    print(f"Track entries: {len(tracks)}")
    print(f"Unique track ids: {len(track_by_id)}")
    print(f"Unique audio paths: {len(unique_paths)}")
    print(f"Playlist files: {len(playlist_files)}")
    print(f"Playlist entries: {total_playlist_entries}")
    print(f"Active playlist: {station.get('active_playlist')}")
    print(f"Media base URL: {media_base_url}")


if __name__ == "__main__":
    main()
