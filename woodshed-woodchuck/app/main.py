import os
import logging
import base64
import hashlib
import hmac
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .safe_logging import SafeRequestLogContext, install_safe_logging

install_safe_logging()

import qrcode
import qrcode.image.svg

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .session_revocations import RevocableSessionMiddleware as SessionMiddleware

from .practice_duration import format_minutes
from .account_routes import (
    current_profile,
    page_generation,
    router as account_router,
)
from .membership_routes import router as membership_router
from .verifier_routes import (
    current_verifier,
    router as verifier_router,
)
from .practice_chart_routes import router as practice_chart_router
from .band_director_dashboard import dashboard_metrics, dashboard_csv
from .trusted_verifier_dashboard import verifier_dashboard_snapshot
from .verifiers import VERIFIER_ROLE_LABELS
from .contests import router as contest_router
from .contest_admin import router as contest_admin_router
from .director_dashboard import router as director_router
from .teams import has_band_director_capability, router as team_router
from .xp_routes import router as xp_router
from .store_routes import router as store_router
from .arcade_routes import router as arcade_router
from .analytics import observe_response
from .analytics_routes import router as analytics_router
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
from .instruments import instrument_definition_payloads, shed_artwork_url, shed_character_url
from .history_mystery import (
    history_mystery_central_date,
)
from .models import WoodchuckState
from .age_routes import router as age_router
from .age_privacy import AgeScreenRequired
from .board_seasons import board_season_presentation
from .seasons import season_covering_date
from .session_config import session_secret, secure_session_cookie, is_production
from .login_limits import protection_status
from .tester_enrollments import (
    C001,
    SESSION_REGISTRATION_CONTEXT,
    establish_registration_context,
    c001_registration_open,
    registration_context,
)

BASE_DIR = Path(__file__).resolve().parent.parent

SESSION_SECRET = session_secret()
SESSION_COOKIE_SECURE = secure_session_cookie()
if is_production():
    logging.getLogger(__name__).warning(
        "Authentication rate limiting: %s; deployment verification still required.",
        protection_status(check_backend=False)["state"],
    )


local_kws_test = os.getenv('KWS_TEST_RUNTIME_MODE') == 'local'
app = FastAPI(title="Woodshed Woodchuck", docs_url=None if local_kws_test else '/docs',
              redoc_url=None if local_kws_test else '/redoc',
              openapi_url=None if local_kws_test else '/openapi.json')
if local_kws_test:
    from .kws_test_safety import bootstrap_local_delivery
    bootstrap_local_delivery()
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
    app.add_middleware(HTTPSRedirectMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[os.environ['KWS_TEST_PUBLIC_HOST'], '127.0.0.1', 'localhost'])
app.add_middleware(SafeRequestLogContext)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_COOKIE_SECURE,
)
app.include_router(account_router)
app.include_router(age_router)

@app.exception_handler(AgeScreenRequired)
async def age_screen_required_response(request, error):
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/account/age",303,headers={"Cache-Control":"no-store"})
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail":error.detail,"age_screen_required":True,"next":"/account/age"},403,headers=error.headers)
app.include_router(membership_router)
app.include_router(verifier_router)
app.include_router(practice_chart_router)
app.include_router(contest_router)
app.include_router(contest_admin_router)
app.include_router(team_router)
app.include_router(director_router)
app.include_router(xp_router)
app.include_router(store_router)
app.include_router(arcade_router)
app.include_router(analytics_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["practice_duration"] = format_minutes


NAV_ITEMS = [
    {"label": "SHED", "href": "/home", "key": "home"},
    {"label": "BOOK", "href": "/p-book", "key": "p_book"},
    {"label": "BOARD", "href": "/quest", "key": "quest"},
    {"label": "SHOP", "href": "/store", "key": "store"},
]


def _render(request: Request, template_name: str, *, analytics_event: str | None = None, **context: object):
    account_state_bootstrap = None
    authenticated_profile = None
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is not None:
            page_generation(request)
            authenticated_profile = {
                # Presentation-only account scope for the daily Arcade entrance.
                "world_entry_account": hmac.new(
                    SESSION_SECRET.encode(),
                    f"world-entry:daily:v2:{profile.id}".encode(),
                    hashlib.sha256,
                ).hexdigest(),
                "display_name": profile.display_name,
                "woodchuck_id": profile.woodchuck_id,
                "band_director": has_band_director_capability(
                    session, profile_id=profile.id
                ),
            }
            saved_state = session.get(WoodchuckState, profile.id)
            account_state_bootstrap = {
                "state": saved_state.state_json if saved_state else None,
                "revision": saved_state.revision if saved_state else 0,
            }

    response = templates.TemplateResponse(
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
            "authenticated_profile": authenticated_profile,
            **context,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    if analytics_event is not None:
        return observe_response(
            response, session_factory=SessionLocal,
            profile_id=profile.id if profile is not None else None,
            event_type=analytics_event,
        )
    return response


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


@app.get("/teams/director/manage")
def director_team_management_page(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            return RedirectResponse(url="/login", status_code=303)
        if not has_band_director_capability(session, profile_id=profile.id):
            return RedirectResponse(url="/home", status_code=303)
    return RedirectResponse(url="/director", status_code=303)


@app.get("/director")
def director_dashboard_page(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            return RedirectResponse(url="/login", status_code=303)
        if not has_band_director_capability(session, profile_id=profile.id):
            return RedirectResponse(url="/home", status_code=303)
    return _render(
        request,
        "director_dashboard.html",
        title="Director Dashboard",
        active_nav="director",
        page_class="main-app-page director-dashboard-page",
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
        title="Verifier Sign In",
        active_nav=None,
    )


@app.get("/trusted-verifiers/dashboard")
def trusted_verifier_dashboard_page(request: Request, connection_id: int | None = None):
    with SessionLocal() as session:
        verifier = current_verifier(request, session)

        if verifier is None:
            return RedirectResponse(
                url="/trusted-verifiers/login",
                status_code=303,
            )

        try:
            snapshot = verifier_dashboard_snapshot(session, verifier_id=verifier.id,
                                                   connection_id=connection_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error),
                                headers={"Cache-Control": "no-store"}) from error
        return templates.TemplateResponse(
            request=request, name="trusted_verifier_dashboard.html",
            context={"title": "Verifier Dashboard", "verifier_name": verifier.display_name,
                     "verifier_email": verifier.email, **snapshot},
            headers={"Cache-Control": "no-store"},
        )


@app.get("/band-director/dashboard")
@app.get("/band-director/dashboard.csv")
def band_director_dashboard_page(request: Request, week: date | None = None):
    with SessionLocal() as session:
        verifier = current_verifier(request, session)
        if verifier is None:
            return RedirectResponse(url="/trusted-verifiers/login", status_code=303)
        try:
            metrics = dashboard_metrics(session, verifier_id=verifier.id, selected_week=week)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if request.url.path.endswith("/dashboard.csv"):
            return Response(
                content=dashboard_csv(metrics), media_type="text/csv; charset=utf-8",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                         "Content-Disposition": f'attachment; filename="woodshed-band-director-{metrics["selected_week"]}.csv"'},
            )
        # Adult pages must not bootstrap an unrelated signed-in player account.
        return templates.TemplateResponse(
            request=request,
            name="band_director_dashboard.html",
            context={
                "title": "Band Director Dashboard",
                "verifier_name": verifier.display_name,
                "verifier_email": verifier.email,
                "page_class": "band-director-page",
                **metrics,
            },
            headers={"Cache-Control": "no-store"},
        )


@app.get("/trusted-verifiers/accept/{token}")
def trusted_verifier_accept_page(
    request: Request,
    token: str,
):
    return _render(
        request,
        "trusted_verifier_accept.html",
        title="Accept Adult Invitation",
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
        title="Verifiers & Band Director",
        verifier_role_labels=VERIFIER_ROLE_LABELS,
        active_nav="home",
        return_to_p_book=(request.query_params.get("return_to") == "p-book"),
        # This page already shared SHED's fixed navigation and lower sound
        # position; retain that existing utility-page behavior.
        page_class="main-app-page",
    )


@app.get("/guest")
@app.get("/guest/login")
def guest_page(request: Request):
    # Read the existing account and its page-freshness marker only. Do not
    # bootstrap account state or observe Guest activity.
    with SessionLocal() as session:
        profile = current_profile(request, session)
        account_id = profile.woodchuck_id if profile is not None else ""
        if profile is not None:
            page_generation(request)
    # The signed C001 claim is registration context, not authentication. All
    # other account/adult/admin session state still requires explicit logout.
    tester_claim = registration_context(request)
    context_only = bool(tester_claim) and set(request.session) == {SESSION_REGISTRATION_CONTEXT}
    blocked = bool(profile is not None or (request.session and not context_only))
    signing_in = request.url.path == "/guest/login" and not blocked
    response = templates.TemplateResponse(
        request=request, name="guest.html",
        context={"blocked": blocked, "signing_in": signing_in,
                 "account_id": account_id, "instruments": INSTRUMENT_OPTIONS,
                 "levels": LEVEL_OPTIONS, "goals": GOAL_OPTIONS,
                 "c001_registration": bool(tester_claim) and not blocked},
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; connect-src "
        + ("'self'" if blocked or signing_in else "'none'")
        + "; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


@app.get("/prebeta/C001")
def prebeta_c001(request: Request):
    """Deliberate, public C001 entry; no account or enrollment is created."""
    try:
        establish_registration_context(request, C001)
    except ValueError as error:
        return templates.TemplateResponse(
            request=request, name="c001_entry.html",
            context={"message": str(error)}, status_code=503,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )
    response = RedirectResponse(url="/guest", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/prebeta/C001/display")
def c001_display(request: Request):
    # The established public site URL is independent of Host, cookies and query strings.
    entry_url = SHOP_SHARE_URL.rstrip("/") + "/prebeta/C001"
    return templates.TemplateResponse(
        request=request, name="c001_entry.html",
        context={"entry_url": entry_url, "entry_qr": qr_data_uri(entry_url),
                 "registration_open": c001_registration_open()},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@app.get("/setup")
def setup(request: Request):
    return _render(
        request,
        "setup.html",
        selected_age=request.query_params.get("age"),
        title="Setup Your Musician",
        instruments=INSTRUMENT_OPTIONS,
        levels=LEVEL_OPTIONS,
        goals=GOAL_OPTIONS,
        c001_registration=registration_context(request) is not None,
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
    character_url = shed_character_url(None)
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is not None:
            character_url = shed_character_url(profile.instrument)
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
        shed_cabin_background_url=shed_artwork_url(None),
        shed_character_url=character_url,
    )


@app.get("/p-book")
def p_book(request: Request):
    from .feature_access import can_use_feature
    with SessionLocal() as session:
        profile = current_profile(request, session)
        feature_access = {"practice_insights": bool(profile and can_use_feature(
            session, profile.id, "practice_insights"))}
    response = _render(
        request, "p_book.html", title="book", active_nav="p_book",
        page_class="main-app-page", feature_access=feature_access,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/practice/pristine")
def pristine_practice(request: Request):
    return _render(
        request,
        "pristine_practice.html",
        analytics_event="pristine_entered",
        title="Pristine Practice",
        active_nav="store",
        page_class="main-app-page pristine-practice-screen",
    )


@app.get("/quest")
def quest(request: Request):
    member_since = None
    with SessionLocal() as session:
        board_season = board_season_presentation(season_covering_date(
            session, datetime.now(ZoneInfo("America/Chicago")).date()
        ))
        profile = current_profile(request, session)
        if profile is not None:
            created_at = profile.created_at
            member_since = {
                "timestamp": created_at.isoformat(),
                "compact": created_at.strftime("%b %Y"),
                "full": created_at.strftime("%B %d, %Y"),
            }
    return _render(
        request, "quest.html", title="board", active_nav="quest",
        page_class="main-app-page", member_since=member_since,
        board_season=board_season,
    )


@app.get("/plunge-burrow")
def plunge_burrow(request: Request):
    return _render(
        request,
        "plunge_burrow.html",
        title="Plunge Burrow",
        active_nav="store",
    )


@app.get("/arcade")
def arcade(request: Request):
    return _render(
        request,
        "arcade.html",
        analytics_event="arcade_entered",
        title="Arcade",
        active_nav="store",
        page_class="main-app-page arcade-screen",
    )


@app.get("/arcade/blue")
def arcade_blue(request: Request):
    return _render(
        request,
        "arcade_game.html",
        title="Blue",
        active_nav="store",
        page_class="main-app-page arcade-screen",
        arcade_game={
            "key": "blue",
            "name": "Blue",
            "description": "",
        },
    )


@app.get("/arcade/radio-tuner")
def arcade_radio_tuner(request: Request):
    return _render(
        request,
        "arcade_game.html",
        title="Radio Tuner",
        active_nav="store",
        page_class="main-app-page arcade-screen",
        arcade_game={
            "key": "radio-tuner",
            "name": "Radio Tuner",
            "description": (
                "You have 30 seconds to tap the needle when it is in the "
                "gold zone as many times as you can."
            ),
        },
    )


@app.get("/arcade/wheel-of-woodchuck")
def arcade_wheel_of_woodchuck(request: Request):
    with SessionLocal() as session:
        if current_profile(request, session) is None:
            return RedirectResponse(url="/login", status_code=303)
    return _render(
        request,
        "wheel_of_woodchuck.html",
        title="Wheel of Woodchuck",
        active_nav="store",
        page_class="main-app-page arcade-screen",
    )


@app.get("/arcade/scale-keyboard")
def arcade_scale_keyboard(request: Request):
    with SessionLocal() as session:
        if current_profile(request, session) is None:
            return RedirectResponse(url="/login", status_code=303)
    return _render(
        request,
        "scale_keyboard.html",
        title="Scale Keyboard",
        active_nav="store",
        page_class="main-app-page arcade-screen",
    )


@app.get("/arcade/thirds")
def arcade_thirds(request: Request):
    with SessionLocal() as session:
        if current_profile(request, session) is None:
            return RedirectResponse(url="/login", status_code=303)
    return _render(
        request,
        "thirds.html",
        title="Thirds",
        active_nav="store",
        page_class="main-app-page arcade-screen",
    )


@app.get("/arcade/dressed-to-the-nines")
def arcade_dressed_to_the_nines(request: Request):
    with SessionLocal() as session:
        if current_profile(request, session) is None:
            return RedirectResponse(url="/login", status_code=303)
    return _render(
        request,
        "dressed_to_the_nines.html",
        title="Dressed to the Nines",
        active_nav="store",
        page_class="main-app-page arcade-screen",
    )


@app.get("/arcade/interval-basic-training")
def arcade_interval_basic_training(request: Request):
    with SessionLocal() as session:
        if current_profile(request, session) is None:
            return RedirectResponse(url="/login", status_code=303)
    return _render(
        request,
        "interval_basic_training.html",
        title="Interval Basic Training",
        active_nav="store",
        page_class="main-app-page arcade-screen",
    )


@app.get("/arcade/history-mystery")
def arcade_history_mystery(request: Request):
    with SessionLocal() as session:
        if current_profile(request, session) is None:
            return RedirectResponse(url="/login", status_code=303)
    play_date = history_mystery_central_date()
    return _render(
        request,
        "history_mystery.html",
        title="History Mystery",
        active_nav="store",
        page_class="main-app-page arcade-screen",
        history_mystery_date=play_date.isoformat(),
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

from .age_privacy import PublicationCacheBoundary
app.add_middleware(PublicationCacheBoundary)

from .family_routes import router as family_router
app.include_router(family_router)
from .kws_routes import router as kws_router
app.include_router(kws_router)
if local_kws_test:
    from .kws_test_safety import LocalCallbackEvidence
    app.add_middleware(LocalCallbackEvidence)
