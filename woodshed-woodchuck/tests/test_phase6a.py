from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import account_routes
from app.account_routes import DailySecretSubmission
from app.db import Base
from app.models import RewardGrant, WoodchuckProfile, WoodchuckState


ROOT = Path(__file__).resolve().parents[1]


def request_for(profile_id: int | None) -> Request:
    session = {}
    if profile_id is not None:
        session[account_routes.SESSION_PROFILE_ID] = profile_id
    return Request({"type": "http", "method": "POST", "path": "/account/daily-secret", "headers": [], "query_string": b"", "session": session})


def test_daily_secret_is_server_validated_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        profile = WoodchuckProfile(woodchuck_id="WC-SECRET", display_name="Secret", pin_hash="private", instrument="Flute", level="Beginner", goal="Practice")
        session.add(profile); session.flush()
        session.add(WoodchuckState(profile_id=profile.id, state_json={"progress": {"credits": 5}}, revision=2))
        session.commit(); profile_id = profile.id
    monkeypatch.setattr(account_routes, "SessionLocal", sessions)
    real_datetime = datetime
    class FrozenDateTime(datetime):
        current = real_datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(account_routes, "datetime", FrozenDateTime)

    with pytest.raises(HTTPException):
        account_routes.redeem_daily_secret(request_for(profile_id), DailySecretSubmission(passcode="wrong"))
    first = account_routes.redeem_daily_secret(request_for(profile_id), DailySecretSubmission(passcode="  UnIoN  "))
    duplicate = account_routes.redeem_daily_secret(request_for(profile_id), DailySecretSubmission(passcode="union"))
    assert first == {"redeemed": True, "amount": 20, "credits": 25, "revision": 3}
    assert duplicate["redeemed"] is False and duplicate["amount"] == 0
    FrozenDateTime.current = real_datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    next_day = account_routes.redeem_daily_secret(request_for(profile_id), DailySecretSubmission(passcode="UNION"))
    assert next_day["redeemed"] is True and next_day["credits"] == 45
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(RewardGrant)) == 2
        grants = session.scalars(select(RewardGrant)).all()
        assert all(grant.amount == 20 and grant.source_key.startswith("daily-secret:") for grant in grants)
        assert session.get(WoodchuckState, profile_id).state_json["progress"]["credits"] == 45


def test_secret_client_accessibility_and_no_client_passcode_leak() -> None:
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert home.count('id="shed-secret-button"') == 1
    assert 'type="button"' in home and 'aria-controls="shed-secret-panel"' in home
    assert 'label for="shed-secret-passcode"' in home
    assert "bottom: 0.45rem" in css and "left: 0.45rem" in css
    assert "union" not in (home + javascript).casefold()
    assert 'feedback.textContent = payload.redeemed ? "+20 dandelions"' in javascript
    assert "celebrateSuccess(form)" in javascript
    assert "prefers-reduced-motion: reduce" in javascript


def test_book_phase6a_timer_text_structure_and_stone_hooks() -> None:
    book = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    content = (ROOT / "app/content.py").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    definition = "Anytime you are messing with your instrument—giving it attention in any way—counts as practice. Minutes spent thinking about your instrument can count as half-minutes. We’re on the honor system here!"
    assert "Preset email addresses" in book
    assert "Do not submit your band director or teacher's email address without talking with them first, please!" in book
    assert "P-Chart sharing contacts" not in book
    assert "Choose one connected" not in book + javascript
    assert definition not in book and definition in content
    assert "PRACTICE_TIMER_LIMIT_SECONDS = 120 * 60" in javascript
    assert "Math.min(" in javascript and 'minutesEl.value = "120"' in javascript
    assert "Timer stopped at 2 hours. You can adjust your minutes before submitting." in javascript
    assert "sessionStorage" in javascript and "form.requestSubmit()" not in javascript[javascript.index("function wirePracticeTimer"):javascript.index("function getRecentEmails")]
    assert "p-book-bold-divider" in book + css and "p-book-lower-region" in book + css
    assert book.count("practice-stat-stone") == 4
    assert ".practice-stat-stone" in css and "pirate-logbook" in book[book.index("p-book-lower-region"):]


def test_board_phase6a_feedback_success_and_placeholder_contract() -> None:
    board = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert 'id="trivia-selected-answer"' in board
    assert "Your answer: ${daily.triviaSelectedAnswer}" in javascript
    assert 'fetch("/contests/trivia/answer"' in javascript
    assert "checkedAnswer.correct === true" in javascript
    assert "No reward was earned today" in javascript
    assert "Completed · +1 Camp Point" not in javascript
    assert "+1 Camp Point · +1 dandelion" in javascript
    assert "confirmed-checkmark" in javascript + css
    assert '<span class="sr-only"> Completed</span>' in javascript
    assert "is-confirmed-success" in javascript + css
    assert 'id="plunge-burrow-button"' in board and "🕳️" in board and "Coming Soon" in board
    plunge = javascript[javascript.index("function wirePlungeBurrow"):javascript.index("function wireBandCampStandings")]
    assert "fetch(" not in plunge and "saveState" not in plunge and "award" not in plunge.casefold()
    assert 'id="complete-quest-btn"' in board and "Quest Complete" in javascript
    assert "The quest is complete. Extra practice" not in board
