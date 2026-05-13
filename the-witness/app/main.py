from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="The Witness")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


PROFILES = [
    {
        "id": "musician",
        "name": "The Musician",
        "xp": 80,
        "goal": 100,
        "summary": "Practice, rhythm, tone, creativity, performance.",
        "unlocked": False,
        "reward": "Music-note pin",
    },
    {
        "id": "athlete",
        "name": "The Athlete",
        "xp": 45,
        "goal": 100,
        "summary": "Movement, strength, sports, conditioning.",
        "unlocked": False,
        "reward": "Sneakers",
    },
    {
        "id": "scholar",
        "name": "The Scholar",
        "xp": 65,
        "goal": 100,
        "summary": "Learning, study, curiosity, technical growth.",
        "unlocked": False,
        "reward": "Book satchel",
    },
    {
        "id": "helping-hand",
        "name": "The Helping Hand",
        "xp": 100,
        "goal": 100,
        "summary": "Service, support, showing up for others.",
        "unlocked": True,
        "reward": "Heart patch",
    },
    {
        "id": "builder",
        "name": "The Builder",
        "xp": 70,
        "goal": 100,
        "summary": "Creating, fixing, coding, shipping, improving systems.",
        "unlocked": False,
        "reward": "Tool belt",
    },
    {
        "id": "plant",
        "name": "The Plant",
        "xp": 90,
        "goal": 100,
        "summary": "Growth, patience, sunlight, roots, consistency.",
        "unlocked": False,
        "reward": "Sprout backpack",
    },
]


RECENT_WINS = [
    "Practiced sax for 30 minutes.",
    "Helped a friend troubleshoot their laptop.",
    "Ate a real breakfast instead of skipping food.",
]


AVATAR_ITEMS = [
    {"name": "Heart patch", "source": "The Helping Hand", "equipped": True},
    {"name": "Music-note pin", "source": "The Musician", "equipped": False},
    {"name": "Book satchel", "source": "The Scholar", "equipped": False},
    {"name": "Sprout backpack", "source": "The Plant", "equipped": False},
]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "profiles": PROFILES,
            "recent_wins": RECENT_WINS,
        },
    )


@app.get("/log-win", response_class=HTMLResponse)
def log_win(request: Request):
    suggested_xp = [
        {"profile": "The Musician", "xp": 40},
        {"profile": "The Scholar", "xp": 10},
        {"profile": "The Plant", "xp": 10},
    ]

    return templates.TemplateResponse(
        request,
        "log_win.html",
        {
            "suggested_xp": suggested_xp,
        },
    )


@app.get("/trophy-case", response_class=HTMLResponse)
def trophy_case(request: Request):
    return templates.TemplateResponse(
        request,
        "trophy_case.html",
        {
            "profiles": PROFILES,
        },
    )


@app.get("/bobblehead/{profile_id}", response_class=HTMLResponse)
def bobblehead_detail(request: Request, profile_id: str):
    profile = next((p for p in PROFILES if p["id"] == profile_id), None)

    return templates.TemplateResponse(
        request,
        "bobblehead_detail.html",
        {
            "profile": profile,
        },
    )


@app.get("/closet", response_class=HTMLResponse)
def closet(request: Request):
    return templates.TemplateResponse(
        request,
        "closet.html",
        {
            "avatar_items": AVATAR_ITEMS,
        },
    )
