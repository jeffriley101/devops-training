from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import account_routes, contests
from app.account_routes import DailySecretSubmission
from app.contests import TriviaAnswerSubmission
from app.db import Base
from app.models import CampPointAward, DailyTriviaAttempt, RewardGrant, WoodchuckProfile, WoodchuckState


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
    definition = "Practice counts whenever you give your instrument real attention—playing it, cleaning it, fingering through music, or thinking about what you want to improve. Thinking-only time counts as half-minutes. We trust you to keep it honest!"
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


def test_book_actions_precede_divider_and_blue_statistics_region() -> None:
    book = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    action_positions = [book.index(label) for label in (
        "Submit to Log Book", "Copy to Clipboard", "Email Your Chart",
    )]
    divider = book.index("p-book-bold-divider")
    lower = book.index("p-book-lower-region")
    stats = book.index("This Week’s Practice")
    logbook = book.index("pirate-logbook")
    assert max(action_positions) < divider < lower < stats < logbook
    assert "p-book-lower-spacer" in book and ".p-book-lower-spacer" in css
    assert "background: linear-gradient" in css[css.index(".p-book-lower-region"):css.index(".practice-stat-stone")]
    assert "p-book-lower-actions" not in book + css


def test_board_phase6a_feedback_success_and_placeholder_contract() -> None:
    board = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")
    assert 'id="trivia-selected-answer"' not in board
    assert "Your answer:" not in board + javascript
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
    assert 'id="quest-feedback"' not in board
    assert 'id="instrument-advice"' not in board
    assert 'id="practice-form"' in board


def test_board_trivia_and_visible_copy_refinements() -> None:
    board = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    contests_source = (ROOT / "app/contests.py").read_text(encoding="utf-8")
    assert "Band Camp Bonus" in board and "Band Camp Hours Bonus" not in board
    assert '"marching": "marching", "hours": "band-camp-hours"' in contests_source
    assert "Keep getting faster... Then one day we will show you double-tonguing!" not in board + javascript
    assert "serverConfirmedTriviaAttempt !== null" in javascript
    assert "selected_answer_text: checkedAnswer.selected_answer_text" in javascript
    assert "daily.triviaSelectedAnswer = triviaAttempt.selected_answer_text" in javascript
    assert "Attempt used" in javascript and "no reward earned" in javascript
    assert "persistCampPoint(\"trivia\")" not in javascript
    for index in range(10):
        assert f"Your answer: {index}" not in board + javascript


def test_trivia_attempt_persists_text_and_rewards_only_correct_once(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        wrong_profile = WoodchuckProfile(woodchuck_id="WC-WRONG", display_name="Wrong", pin_hash="private", instrument="Flute", level="Beginner", goal="Practice")
        correct_profile = WoodchuckProfile(woodchuck_id="WC-RIGHT", display_name="Right", pin_hash="private", instrument="Flute", level="Beginner", goal="Practice")
        session.add_all([wrong_profile, correct_profile]); session.commit()
        wrong_id, correct_id = wrong_profile.id, correct_profile.id
    monkeypatch.setattr(contests, "SessionLocal", sessions)
    real_datetime = datetime
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(contests, "datetime", FrozenDateTime)

    wrong = contests.check_trivia_answer(
        request_for(wrong_id), TriviaAnswerSubmission(activity_date=datetime(2026, 7, 31).date(), selected_index=1)
    )
    wrong_retry = contests.check_trivia_answer(
        request_for(wrong_id), TriviaAnswerSubmission(activity_date=datetime(2026, 7, 31).date(), selected_index=0)
    )
    correct = contests.check_trivia_answer(
        request_for(correct_id), TriviaAnswerSubmission(activity_date=datetime(2026, 7, 31).date(), selected_index=0)
    )
    correct_retry = contests.check_trivia_answer(
        request_for(correct_id), TriviaAnswerSubmission(activity_date=datetime(2026, 7, 31).date(), selected_index=2)
    )

    assert wrong["selected_answer_text"] == wrong_retry["selected_answer_text"] == "Diminuendo"
    assert wrong["correct"] is False and wrong["award"] is None
    assert correct["selected_answer_text"] == correct_retry["selected_answer_text"] == "Crescendo"
    assert correct["award_created"] is True and correct_retry["award_created"] is False
    refreshed = contests.daily_camp_point_awards(datetime(2026, 7, 31).date(), request_for(wrong_id))
    assert refreshed["trivia_attempt"] == {"selected_answer_text": "Diminuendo", "correct": False}
    assert not {"profile_id", "woodchuck_id", "pin_hash"}.intersection(refreshed)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailyTriviaAttempt)) == 2
        assert session.scalar(select(func.count()).select_from(CampPointAward)) == 1
