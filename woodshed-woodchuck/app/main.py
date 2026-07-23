import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .account_routes import (
    current_profile,
    router as account_router,
)
from .verifier_routes import (
    current_verifier,
    router as verifier_router,
)
from .db import SessionLocal
from .content import (
    GOAL_OPTIONS,
    INSTRUMENT_OPTIONS,
    LEVEL_OPTIONS,
    QUEST_POOL,
    SAX_VIKING_MESSAGES,
    SAX_VIKING_WELCOME,
)

BASE_DIR = Path(__file__).resolve().parent.parent

SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    "woodshed-local-development-secret",
)
SESSION_COOKIE_SECURE = os.getenv(
    "SESSION_COOKIE_SECURE",
    "",
).lower() in {"1", "true", "yes"}


app = FastAPI(title="Woodshed Woodchuck")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_COOKIE_SECURE,
)
app.include_router(account_router)
app.include_router(verifier_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


NAV_ITEMS = [
    {"label": "SHED", "href": "/home", "key": "home"},
    {"label": "BOOK", "href": "/p-book", "key": "p_book"},
    {"label": "BOARD", "href": "/quest", "key": "quest"},
    {"label": "SHOP", "href": "/store", "key": "store"},
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


@app.get("/login")
def login_page(request: Request):
    return _render(
        request,
        "login.html",
        title="Sign In",
        active_nav=None,
    )


@app.get("/trusted-verifiers/login")
def trusted_verifier_login_page(request: Request):
    with SessionLocal() as session:
        verifier = current_verifier(request, session)

        if verifier is not None:
            return RedirectResponse(
                url="/trusted-verifiers/dashboard",
                status_code=303,
            )

    return _render(
        request,
        "trusted_verifier_login.html",
        title="Trusted Verifier Sign In",
        active_nav=None,
    )


@app.get("/trusted-verifiers/dashboard")
def trusted_verifier_dashboard_page(request: Request):
    with SessionLocal() as session:
        verifier = current_verifier(request, session)

        if verifier is None:
            return RedirectResponse(
                url="/trusted-verifiers/login",
                status_code=303,
            )

    return _render(
        request,
        "trusted_verifier_dashboard.html",
        title="Trusted Verifier Dashboard",
        active_nav=None,
    )


@app.get("/trusted-verifiers/accept/{token}")
def trusted_verifier_accept_page(
    request: Request,
    token: str,
):
    return _render(
        request,
        "trusted_verifier_accept.html",
        title="Accept Trusted Verifier Invitation",
        active_nav=None,
        invitation_token=token,
    )


@app.get("/trusted-verifiers")
def trusted_verifiers_page(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)

        if profile is None:
            return RedirectResponse(
                url="/login",
                status_code=303,
            )

    return _render(
        request,
        "trusted_verifiers.html",
        title="Trusted Verifiers",
        active_nav="home",
    )


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
