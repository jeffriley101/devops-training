from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.radio import build_station_tracks, load_playlist, load_station_config, load_tracks

app = FastAPI(title="Hoojshwah Radio")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "station_name": "Hoojshwah Radio",
        },
    )


@app.get("/api/station")
def station():
    station_config = load_station_config()
    tracks = load_tracks()
    playlist = load_playlist(station_config["active_playlist"])
    station_tracks = build_station_tracks(
        tracks=tracks,
        media_base_url=station_config["media_base_url"],
        playlist=playlist,
    )
    total_duration = sum(track["duration_seconds"] for track in station_tracks)

    return {
        **station_config,
        "active_playlist_title": playlist.get("title"),
        "total_duration_seconds": total_duration,
        "tracks": station_tracks,
    }
