#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATION_PATH = ROOT / "data" / "station.json"
TRACKS_PATH = ROOT / "data" / "tracks.json"
VALIDATE_SCRIPT = ROOT / "scripts" / "validate_station_data.py"
OBJECT_LIST_PATH = ROOT / "docs" / "r2-audio-object-list.txt"


def load_json(path):
    return json.loads(path.read_text())


def cmd_validate(_args):
    result = subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT)],
        check=False,
    )
    return result.returncode


def cmd_summary(_args):
    station = load_json(STATION_PATH)
    tracks = load_json(TRACKS_PATH)
    unique_paths = sorted({track["audio_path"] for track in tracks})

    print("Hoojshwah Radio summary")
    print("-----------------------")
    print(f"Station name: {station.get('station_name')}")
    print(f"Active playlist: {station.get('active_playlist')}")
    print(f"Media base URL: {station.get('media_base_url')}")
    print(f"Track entries: {len(tracks)}")
    print(f"Unique audio paths: {len(unique_paths)}")
    return 0


def cmd_list_tracks(_args):
    tracks = load_json(TRACKS_PATH)

    for index, track in enumerate(tracks, start=1):
        title = track.get("title", "Untitled")
        track_type = track.get("type", "track")
        duration = track.get("duration_seconds", "?")
        audio_path = track.get("audio_path", "")

        print(f"{index:03d}. [{track_type}] {title} — {duration}s — {audio_path}")

    return 0


def cmd_check_audio(_args):
    station = load_json(STATION_PATH)
    tracks = load_json(TRACKS_PATH)
    media_base_url = station["media_base_url"].rstrip("/")

    urls = sorted({
        f"{media_base_url}/{track['audio_path'].lstrip('/')}"
        for track in tracks
    })

    bad = []

    for url in urls:
        result = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                "10",
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                url,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        status = result.stdout.strip()
        if status != "200":
            bad.append((status or "NO_STATUS", url))

    if bad:
        print("Missing or unreachable audio URLs:")
        for status, url in bad:
            print(f"{status}  {url}")
        return 1

    print(f"All {len(urls)} unique audio URLs are reachable.")
    return 0


def cmd_unused_audio(_args):
    tracks = load_json(TRACKS_PATH)
    used_paths = {track["audio_path"] for track in tracks}

    if not OBJECT_LIST_PATH.exists():
        print(f"ERROR: Missing object inventory: {OBJECT_LIST_PATH}")
        return 1

    uploaded_paths = {
        f"audio/{line.strip()}"
        for line in OBJECT_LIST_PATH.read_text().splitlines()
        if line.strip()
    }

    unused = sorted(uploaded_paths - used_paths)
    missing_from_inventory = sorted(used_paths - uploaded_paths)

    print("R2 audio inventory comparison")
    print("-----------------------------")
    print(f"Uploaded audio objects: {len(uploaded_paths)}")
    print(f"Used audio paths: {len(used_paths)}")
    print(f"Unused uploaded objects: {len(unused)}")
    print(f"Used paths missing from inventory: {len(missing_from_inventory)}")

    if unused:
        print("\nUnused uploaded objects:")
        for path in unused:
            print(f"- {path}")

    if missing_from_inventory:
        print("\nUsed paths missing from inventory:")
        for path in missing_from_inventory:
            print(f"- {path}")
        return 1

    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Admin helper for Hoojshwah Radio station data."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate station data.")
    validate.set_defaults(func=cmd_validate)

    summary = subparsers.add_parser("summary", help="Show station summary.")
    summary.set_defaults(func=cmd_summary)

    list_tracks = subparsers.add_parser("list-tracks", help="List playlist tracks.")
    list_tracks.set_defaults(func=cmd_list_tracks)

    check_audio = subparsers.add_parser(
        "check-audio",
        help="Check that all audio URLs are reachable.",
    )
    check_audio.set_defaults(func=cmd_check_audio)

    unused_audio = subparsers.add_parser(
        "unused-audio",
        help="Show uploaded R2 audio objects not currently used by tracks.json.",
    )
    unused_audio.set_defaults(func=cmd_unused_audio)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
