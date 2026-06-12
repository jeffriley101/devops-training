import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.radio import build_station_tracks, load_playlist, load_station_config, load_tracks

app = FastAPI(title="Hoojshwah Radio")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

BOTTLES_PATH = Path("data/bottles.json")
ALLOWED_BOTTLE_STYLES = {"green", "brown", "clear", "fancy", "jug", "can"}


class BottleCreate(BaseModel):
    style: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=16)


def load_bottles():
    if not BOTTLES_PATH.exists():
        return []

    with BOTTLES_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_bottles(bottles):
    BOTTLES_PATH.parent.mkdir(parents=True, exist_ok=True)

    with BOTTLES_PATH.open("w", encoding="utf-8") as file:
        json.dump(bottles, file, indent=2)
        file.write("\n")


def make_bottle_stamp():
    return datetime.now(timezone.utc).strftime("BOTTLED:%Y%m%d")


def clean_bottle_label(label):
    return " ".join(label.strip().split())[:16]




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


@app.get("/api/bottles")
def bottles():
    return {
        "bottles": load_bottles()
    }


@app.post("/api/bottles")
def create_bottle(bottle: BottleCreate):
    style = bottle.style.strip().lower()
    label = clean_bottle_label(bottle.label)

    if style not in ALLOWED_BOTTLE_STYLES:
        raise HTTPException(status_code=400, detail="Invalid bottle style")

    if not label:
        raise HTTPException(status_code=400, detail="Bottle label is required")

    bottles = load_bottles()
    new_bottle = {
        "id": str(uuid4()),
        "style": style,
        "label": label,
        "stamp": make_bottle_stamp(),
    }

    bottles.append(new_bottle)
    save_bottles(bottles)

    return new_bottle
