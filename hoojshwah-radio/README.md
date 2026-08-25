# Hoojshwah Radio

Hoojshwah Radio is a simulated-live internet radio project for original Hoojshwah music.

The project includes both a browser-based station and a native Android client. The web
application provides the station interface and stream experience, while the Android app
uses native media playback so audio can continue reliably when the screen is locked or
the app is in the background.

## Technology

The project uses:

- Python
- FastAPI
- JavaScript
- HTML / CSS
- Android
- AndroidX Media3
- ExoPlayer
- MediaSessionService
- MediaController
- Gradle
- Git / GitHub
- Render

## Browser station

The FastAPI web application serves the Hoojshwah Radio experience in a standard browser.

It provides the station interface, playback controls, current-track information, and
simulated-live programming behavior.

## Android client

The Android client uses native Media3 playback rather than relying on browser audio.

Its playback architecture includes:

- a MediaSessionService that owns audio playback
- ExoPlayer for media playback
- a MediaController used by the Activity
- background and screen-off playback
- notification and lock-screen media controls
- Bluetooth and headset control support
- WebView integration with the existing station interface
- separation between browser playback and native Android playback

The Android client is designed so the Activity does not own the player directly, allowing
playback to continue independently of the foreground UI.

## Local web development

From the repository root:

```bash
cd hoojshwah-radio
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## Render deployment

The web application is deployed from the monorepo using:

- Root Directory: `hoojshwah-radio`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Android development

The Android client lives under the project Android application directory.

Current development requirements include:

- Android Studio
- JDK 17
- Android SDK
- Gradle

The Android app is built separately from the Render-hosted web application while using
the same station experience as its user-facing interface.

## Project status

Hoojshwah Radio is an actively developed project.

It has evolved from a browser-only simulated-live station into a combined web and native
Android media application with persistent background playback and native media controls.
