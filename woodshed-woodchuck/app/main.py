import os
import base64
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import qrcode
import qrcode.image.svg

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
from .practice_chart_routes import router as practice_chart_router
from .contests import router as contest_router
from .contest_admin import router as contest_admin_router
from .teams import router as team_router
from .db import SessionLocal
from .content import (
    ART_SUBMISSION_EMAIL,
    GOAL_OPTIONS,
    INSTRUMENT_OPTIONS,
    LEVEL_OPTIONS,
    QUEST_POOL,
    PRACTICE_DEFINITION,
    SAX_VIKING_MESSAGES,
    SAX_VIKING_WELCOME,
    SHOP_SHARE_URL,
)
from .instruments import instrument_definition_payloads
from .models import WoodchuckState

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
app.include_router(practice_chart_router)
app.include_router(contest_router)
app.include_router(contest_admin_router)
app.include_router(team_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


NAV_ITEMS = [
    {"label": "SHED", "href": "/home", "key": "home"},
    {"label": "BOOK", "href": "/p-book", "key": "p_book"},
    {"label": "BOARD", "href": "/quest", "key": "quest"},
    {"label": "SHOP", "href": "/store", "key": "store"},
]


def _render(request: Request, template_name: str, **context: object):
    account_state_bootstrap = None
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is not None:
            saved_state = session.get(WoodchuckState, profile.id)
            account_state_bootstrap = {
                "state": saved_state.state_json if saved_state else None,
                "revision": saved_state.revision if saved_state else 0,
            }

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "nav_items": NAV_ITEMS,
            "active_nav": context.pop("active_nav", None),
            "sax_viking_welcome": SAX_VIKING_WELCOME,
            "quest_pool": QUEST_POOL,
            "sax_viking_messages": SAX_VIKING_MESSAGES,
            "instrument_definitions": instrument_definition_payloads(),
            "account_state_bootstrap": account_state_bootstrap,
            **context,
        },
    )


def public_site_url(request: Request) -> str:
    return SHOP_SHARE_URL


def qr_data_uri(value: str) -> str:
    image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage)
    output = BytesIO()
    image.save(output)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def art_submission_mailto() -> str:
    return f"mailto:{quote(ART_SUBMISSION_EMAIL, safe='@.+-_')}?subject=Woodshed%20Woodchuck%20Artwork"


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
        # This page already shared SHED's fixed navigation and lower sound
        # position; retain that existing utility-page behavior.
        page_class="main-app-page",
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
    member_since = None
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is not None:
            created_at = profile.created_at
            member_since = {
                "timestamp": created_at.isoformat(),
                "compact": created_at.strftime("%b %Y"),
                "full": created_at.strftime("%B %d, %Y"),
            }

    return _render(
        request,
        "home.html",
        title="shed",
        active_nav="home",
        page_class="main-app-page shed-screen",
        instruments=INSTRUMENT_OPTIONS,
        levels=LEVEL_OPTIONS,
        member_since=member_since,
    )


@app.get("/p-book")
def p_book(request: Request):
    return _render(
        request, "p_book.html", title="book", active_nav="p_book",
        page_class="main-app-page",
    )


@app.get("/quest")
def quest(request: Request):
    return _render(
        request, "quest.html", title="board", active_nav="quest",
        page_class="main-app-page",
    )


@app.get("/plunge-burrow")
def plunge_burrow(request: Request):
    return _render(
        request,
        "plunge_burrow.html",
        title="Plunge Burrow",
        active_nav="quest",
    )


@app.get("/store")
def store(request: Request):
    site_url = public_site_url(request)
    return _render(
        request, "store.html", title="shop", active_nav="store",
        page_class="main-app-page",
        public_site_url=site_url, public_site_qr=qr_data_uri(site_url),
        practice_definition=PRACTICE_DEFINITION,
        art_submission_mailto=art_submission_mailto(),
    )
