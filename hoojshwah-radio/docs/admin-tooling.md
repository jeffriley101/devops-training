# Hoojshwah Radio Admin Tooling

This document lists the small admin commands used to check Hoojshwah Radio station data before making changes or deploying.

The helper script is:

hoojshwah-radio/scripts/radio_admin.py

Run all commands from the repo root:

cd ~/Training_scripts

## Available Commands

### Validate station data

Checks that the station config and track metadata are structurally safe.

Command:

python3 hoojshwah-radio/scripts/radio_admin.py validate

### Show station summary

Prints the station name, active playlist, media base URL, total track entries, and unique audio paths.

Command:

python3 hoojshwah-radio/scripts/radio_admin.py summary

### List playlist tracks

Prints the playlist in order with track number, type, title, duration, and audio path.

Command:

python3 hoojshwah-radio/scripts/radio_admin.py list-tracks

### Check R2 audio URLs

Builds each audio URL from media_base_url plus audio_path, then checks that every unique referenced audio file is reachable.

Command:

python3 hoojshwah-radio/scripts/radio_admin.py check-audio

### Show unused uploaded audio

Compares the R2 object inventory against tracks.json and lists audio files that are uploaded but not currently used in the active playlist data.

Command:

python3 hoojshwah-radio/scripts/radio_admin.py unused-audio

## Current Expected Counts

As of the Phase 2 R2 migration:

Track entries: 77
Unique audio paths: 65
Uploaded R2 audio objects: 71
Unused uploaded objects: 6
Used paths missing from inventory: 0

## Why This Exists

Hoojshwah Radio now uses this architecture:

Render = web app / player
GitHub = code + metadata
Cloudflare R2 = MP3 audio library

The admin helper helps prevent broken deploys by checking:

- station metadata
- playlist structure
- R2 audio paths
- reachable audio files
- unused uploaded audio

Run these checks before making playlist, audio, or media URL changes.
