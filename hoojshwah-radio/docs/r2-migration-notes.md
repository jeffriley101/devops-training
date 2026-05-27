# Hoojshwah Radio R2 Audio Migration Notes

## Current architecture

- Render hosts the Hoojshwah Radio web app.
- GitHub stores code and metadata.
- Cloudflare R2 stores MP3 audio files.

## R2 bucket

- Bucket: `hoojshwah-radio-audio`
- Current public development base URL: `https://pub-0ec8c664abcd4f5eb71668b2066f8b3e.r2.dev`
- Audio objects live under the `audio/` prefix.

Example:

```text
audio/arrive.mp3
audio/bumpers/bumper-b.mp3
audio/bumpers/phase1c/sb-17pbm.mp3
```

## Station data

The app stores the media base URL once in:

```text
data/station.json
```

Tracks store only relative audio paths in:

```text
data/tracks.json
```

Example:

```json
"audio_path": "audio/arrive.mp3"
```

The FastAPI backend builds full `audio_url` values for the frontend API response.

## Validation

Run this before deploying station data changes:

```bash
python3 hoojshwah-radio/scripts/validate_station_data.py
```

Expected result:

```text
Station data validation passed.
Track entries: 77
Unique audio paths: 65
```

## Future custom domain

The current `r2.dev` URL is temporary.

Future target may be:

```text
https://media.hoojshwah.com
```

When that domain is ready, update only `media_base_url` in `data/station.json`, then validate and test.
