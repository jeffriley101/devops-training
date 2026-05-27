from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.radio import load_station_config, load_tracks

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
    total_duration = sum(track["duration_seconds"] for track in tracks)

    return {
        **station_config,
        "total_duration_seconds": total_duration,
        "tracks": tracks,
    }
