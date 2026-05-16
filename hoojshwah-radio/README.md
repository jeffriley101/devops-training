# Hoojshwah Radio

An unlisted simulated-live radio station demo for original Hoojshwah music.

## Local development

Run from the project folder:

    cd ~/Training_scripts/hoojshwah-radio
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload

Open:

    http://127.0.0.1:8000

## Render settings

Use these settings when deploying from the monorepo:

- Root Directory: hoojshwah-radio
- Build Command: pip install -r requirements.txt
- Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT

## Audio files

For this first demo, one small MP3 can be committed so Render can serve a working friend-shareable station.

Later, larger audio files should move to public object storage.
