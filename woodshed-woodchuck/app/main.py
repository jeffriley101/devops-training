from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .content import (
    GOAL_OPTIONS,
    INSTRUMENT_OPTIONS,
    LEVEL_OPTIONS,
    QUEST_POOL,
    SAX_VIKING_MESSAGES,
    SAX_VIKING_WELCOME,
)

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="Woodshed Woodchuck")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


NAV_ITEMS = [
    {"label": "shed", "href": "/home", "key": "home"},
    {"label": "book", "href": "/p-book", "key": "p_book"},
    {"label": "board", "href": "/quest", "key": "quest"},
    {"label": "shop", "href": "/store", "key": "store"},
]


def _render(request: Request, template_name: str, **context: object):
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "nav_items": NAV_ITEMS,
            "active_nav": context.pop("active_nav", None),
            "sax_viking_welcome": SAX_VIKING_WELCOME,
            "quest_pool": QUEST_POOL,
            "sax_viking_messages": SAX_VIKING_MESSAGES,
            **context,
        },
    )


@app.get("/")
def welcome(request: Request):
    return _render(request, "welcome.html", title="Woodshed Woodchuck")


@app.get("/setup")
def setup(request: Request):
    return _render(
        request,
        "setup.html",
        title="Setup Your Musician",
        instruments=INSTRUMENT_OPTIONS,
        levels=LEVEL_OPTIONS,
        goals=GOAL_OPTIONS,
        active_nav=None,
    )


@app.post("/setup")
def setup_submit(
    instrument: str = Form(...),
    level: str = Form(...),
    goal: str = Form(...),
):
    # Setup details are persisted client-side in localStorage via JS.
    # This route exists for progressive enhancement / graceful fallback.
    return RedirectResponse(url="/home", status_code=303)


@app.get("/home")
def home(request: Request):
    return _render(request, "home.html", title="shed", active_nav="home")


@app.get("/p-book")
def p_book(request: Request):
    return _render(request, "p_book.html", title="book", active_nav="p_book")


@app.get("/quest")
def quest(request: Request):
    return _render(request, "quest.html", title="board", active_nav="quest")


@app.get("/store")
def store(request: Request):
    return _render(request, "store.html", title="shop", active_nav="store")
