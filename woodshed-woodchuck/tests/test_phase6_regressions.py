import base64
from datetime import date
from copy import deepcopy
import json
from pathlib import Path

import pytest
import qrcode
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import account_routes, contests, main
from app.db import Base
from app.models import DailyTriviaAttempt, WoodchuckProfile


ROOT = Path(__file__).resolve().parents[1]


def database(monkeypatch, module):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(module, "SessionLocal", sessions)
    return sessions


def account_request(profile_id=None):
    session = {}
    if profile_id is not None:
        session[account_routes.SESSION_PROFILE_ID] = profile_id
    return Request({
        "type": "http", "method": "POST", "path": "/account/create",
        "headers": [], "query_string": b"", "session": session,
    })


VALID_CREATE = {
    "display_name": "New Chuck", "pin": "2468", "instrument": "Flute",
    "level": "Beginner", "goal": "Practice every day",
    "initial_state": json.dumps({"progress": {"credits": 3}}),
}


@pytest.mark.parametrize(("change", "message"), [
    ({"display_name": None}, "Please name your Woodchuck."),
    ({"instrument": None}, "Please choose an instrument."),
    ({"instrument": "Kazoo"}, "Please choose a supported instrument."),
    ({"level": None}, "Please choose a level."),
    ({"level": "Wizard"}, "Please choose a supported level."),
    ({"goal": None}, "Please choose a practice goal."),
    ({"pin": "12"}, "Your PIN must contain exactly four digits."),
    ({"initial_state": None}, "The initial Woodshed state is required."),
    ({"initial_state": "{broken"}, "The initial Woodshed state is malformed."),
    ({"initial_state": "[]"}, "The initial Woodshed state must be an object."),
])
def test_each_reachable_create_validation_400_is_safe_and_specific(
    monkeypatch, change, message
) -> None:
    sessions = database(monkeypatch, account_routes)
    fields = {**VALID_CREATE, **change}
    with pytest.raises(HTTPException) as caught:
        account_routes.create_account(account_request(), **fields)
    assert caught.value.status_code == 400
    assert caught.value.detail == message
    with sessions() as session:
        assert session.query(WoodchuckProfile).count() == 0


def test_authenticated_create_400_is_clear_and_does_not_duplicate(monkeypatch) -> None:
    sessions = database(monkeypatch, account_routes)
    with sessions() as session:
        profile = WoodchuckProfile(
            woodchuck_id="WC-EXISTING", display_name="Existing", pin_hash="private",
            instrument="Flute", level="Beginner", goal="Practice",
        )
        session.add(profile)
        session.commit()
        profile_id = profile.id
    with pytest.raises(HTTPException) as caught:
        account_routes.create_account(account_request(profile_id), **VALID_CREATE)
    assert caught.value.status_code == 400
    assert "already signed in" in caught.value.detail
    with sessions() as session:
        assert session.query(WoodchuckProfile).count() == 1


def test_create_http_400_messages_reach_the_browser(monkeypatch) -> None:
    sessions = database(monkeypatch, account_routes)
    client = TestClient(main.app)
    incomplete = client.post("/account/create", data={
        key: value for key, value in VALID_CREATE.items() if key != "goal"
    })
    assert incomplete.status_code == 400
    assert incomplete.json() == {"detail": "Please choose a practice goal."}

    created = client.post("/account/create", data=VALID_CREATE)
    assert created.status_code == 200
    assert created.json()["credentials"]["pin"] == "2468"
    duplicate = client.post("/account/create", data=VALID_CREATE)
    assert duplicate.status_code == 400
    assert "already signed in" in duplicate.json()["detail"]
    with sessions() as session:
        assert session.query(WoodchuckProfile).count() == 1


def test_create_client_surfaces_safe_detail_and_never_uploads_after_success() -> None:
    javascript = (ROOT / "static/js/account.js").read_text(encoding="utf-8")
    flow = javascript[javascript.index("function wireCreateAccount"):javascript.index("function wireLogin")]
    assert "await responseMessage(" in flow
    assert "typeof payload.detail === \"string\"" in javascript
    for message in (
        "Please name your Woodchuck.", "Please choose an instrument.",
        "Please choose a level.", "Please choose a practice goal.",
        "Your PIN must contain exactly four digits.",
    ):
        assert message in flow
    assert 'fetch("/account/state"' not in flow
    assert "await uploadState" not in flow
    assert "payload.credentials.pin" in flow


def contest_request(profile_id: int) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/contests/camp-points/awards/2026-01-01",
        "headers": [], "query_string": b"",
        "session": {account_routes.SESSION_PROFILE_ID: profile_id},
    })


def test_trivia_server_resolves_legacy_indices_and_numeric_answer_text(monkeypatch) -> None:
    sessions = database(monkeypatch, contests)
    activity_date = date(2026, 1, 7)
    with sessions() as session:
        legacy = WoodchuckProfile(
            woodchuck_id="WC-LEGACY", display_name="Legacy", pin_hash="private",
            instrument="Flute", level="Beginner", goal="Practice",
        )
        literal = WoodchuckProfile(
            woodchuck_id="WC-LITERAL", display_name="Literal", pin_hash="private",
            instrument="Flute", level="Beginner", goal="Practice",
        )
        session.add_all([legacy, literal]); session.flush()
        session.add_all([
            DailyTriviaAttempt(profile_id=legacy.id, activity_date=activity_date, selected_answer="2", correct=True),
            DailyTriviaAttempt(profile_id=literal.id, activity_date=activity_date, selected_answer="2", correct=False),
        ])
        session.commit()
        legacy_id, literal_id = legacy.id, literal.id

    legacy_payload = contests.daily_camp_point_awards(activity_date, contest_request(legacy_id))
    literal_payload = contests.daily_camp_point_awards(activity_date, contest_request(literal_id))
    assert legacy_payload["trivia_attempt"] == {"selected_answer_id": "four", "correct": True}
    assert literal_payload["trivia_attempt"] == {"selected_answer_id": "two", "correct": False}
    assert "selected_answer" not in legacy_payload["trivia_attempt"]


def test_trivia_client_never_displays_raw_response_index_and_summarizes_result() -> None:
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    band_camp = javascript[javascript.index("function wireBandCamp"):javascript.index("function wirePlungeBurrow")]
    assert "selected_answer_id" in band_camp
    assert "selected_index" not in band_camp
    assert "Your answer:" not in band_camp
    assert "input.checked = choice.id === selectedAnswerId" in band_camp
    assert "Number(selected.value)" not in band_camp
    assert "+1 Board Activity Point · +1 dandelion" in band_camp
    assert "Attempt used" in band_camp
    assert "Completed; no reward earned" in band_camp


def test_every_canonical_trivia_question_has_one_matching_stable_answer() -> None:
    question_ids = set()
    for question in contests.TRIVIA_QUESTIONS:
        assert question["id"] not in question_ids
        question_ids.add(question["id"])
        choice_ids = [choice["id"] for choice in question["choices"]]
        assert len(choice_ids) == len(set(choice_ids))
        assert choice_ids.count(question["correct_answer_id"]) == 1


def test_crescendo_correctness_survives_choice_reordering(monkeypatch) -> None:
    sessions = database(monkeypatch, contests)
    with sessions() as session:
        profile = WoodchuckProfile(
            woodchuck_id="WC-SHUFFLE", display_name="Shuffle", pin_hash="private",
            instrument="Flute", level="Beginner", goal="Practice",
        )
        session.add(profile); session.commit(); profile_id = profile.id

    questions = deepcopy(contests.TRIVIA_QUESTIONS)
    crescendo = questions[1]
    crescendo["choices"] = tuple(reversed(crescendo["choices"]))
    monkeypatch.setattr(contests, "TRIVIA_QUESTIONS", questions)
    real_datetime = contests.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 7, 30, 12, tzinfo=tz)

    monkeypatch.setattr(contests, "datetime", FrozenDateTime)
    result = contests.check_trivia_answer(
        contest_request(profile_id),
        contests.TriviaAnswerSubmission(
            activity_date=date(2026, 7, 30), selected_answer_id="crescendo",
        ),
    )
    assert result["question"] == "Which word means to gradually get louder?"
    assert result["selected_answer_id"] == "crescendo"
    assert result["correct"] is True
    assert result["award_created"] is True
    public = contests.public_trivia_question(date(2026, 7, 30))
    assert "correct_answer_id" not in public
    assert [choice["id"] for choice in public["choices"]][-1] == "crescendo"


def test_marching_uses_one_pending_request_and_restores_on_failure() -> None:
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    handler = javascript[
        javascript.index("if (marchingButton) {"):
        javascript.index("function wirePlungeBurrow")
    ]
    assert handler.count('persistCampPoint("marching")') == 1
    assert "marchingButton.disabled = true" in handler
    assert 'marchingButton.textContent = "Saving challenge…"' in handler
    assert "marchingButton.disabled = false" in handler
    assert "marchingButton.textContent = readyText" in handler
    assert "persistedAward.created === true" in handler
    assert 'awardContest(next, "marching")' in handler
    assert "confirm" not in handler.casefold()
    assert '"Challenge completed ✓"' in javascript
    assert "details.open = false" in javascript


def test_shop_photo_only_scene_and_unboxed_accessible_emoji_controls() -> None:
    markup = (ROOT / "templates/store.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    scene_css = css[css.index(".shop-scene {"):css.index(".shop-feature-dialog {")]
    assert 'src="/static/img/shop3.png"' in markup
    assert "object-fit: cover" in scene_css
    assert "background: none" in scene_css
    assert ".shop-scene::before" not in css
    assert "border: 3px solid #f4d35e" in scene_css
    assert "background: transparent" in scene_css
    assert "box-shadow: inset 0 0 0 1px rgba(255, 244, 210, 0.25)" in scene_css
    assert "shop-control-label" not in markup
    assert "shop-coming-soon" not in markup
    assert markup.count('class="sr-only"') >= 10
    assert markup.count("aria-label=") >= 12
    assert "min-height: 70px" in scene_css
    assert "width: min(100%, 760px)" in scene_css


def test_qr_library_payload_and_all_share_surfaces_use_canonical_root(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://public.example/private/path?account=private")
    request = Request({
        "type": "http", "method": "GET", "path": "/store", "headers": [],
        "query_string": b"", "scheme": "http", "server": ("127.0.0.1", 8000),
        "session": {},
    })
    canonical = main.public_site_url(request)
    assert canonical == "https://woodshed-woodchuck.onrender.com/"
    qr = qrcode.QRCode()
    qr.add_data(canonical)
    qr.make(fit=True)
    decoded_payload = b"".join(segment.data for segment in qr.data_list).decode("utf-8")
    assert decoded_payload == canonical
    uri = main.qr_data_uri(canonical)
    assert uri.startswith("data:image/svg+xml;base64,")
    assert b"<svg" in base64.b64decode(uri.split(",", 1)[1])

    captured = []
    monkeypatch.setattr(main, "qr_data_uri", lambda value: captured.append(value) or "data:image/svg+xml;base64,SAFE")
    response = main.store(request)
    context = response.context
    assert captured == [canonical]
    assert context["public_site_url"] == canonical
    assert "local_share_fallback" not in context


def test_share_url_never_falls_back_to_local_request_origin(monkeypatch) -> None:
    request = Request({
        "type": "http", "method": "GET", "path": "/store", "headers": [],
        "query_string": b"", "scheme": "http", "server": ("127.0.0.1", 8000),
        "session": {},
    })
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert main.public_site_url(request) == "https://woodshed-woodchuck.onrender.com/"
    assert not hasattr(main, "uses_local_share_fallback")
