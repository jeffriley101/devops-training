from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounts import (
    ProfileChangeCooldown,
    update_profile_display_name,
    update_profile_level,
)
from app.db import Base
from app.models import PracticeChart, WoodchuckProfile
from app.practice_chart_routes import practice_streak, profile_practice_streak


ROOT = Path(__file__).resolve().parents[1]


def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def profile(session):
    row = WoodchuckProfile(
        woodchuck_id="WC-CLEANUP", display_name="Original", pin_hash="private",
        instrument="Clarinet", level="Beginner", goal="Practice",
    )
    session.add(row)
    session.commit()
    return row


def test_name_cooldown_and_private_identity_preservation() -> None:
    sessions = factory()
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
    with sessions() as session:
        row = profile(session)
        update_profile_display_name(session, profile=row, display_name="New Name", now=now)
        assert row.display_name == "New Name"
        changed_at = row.display_name_changed_at
        update_profile_display_name(session, profile=row, display_name="New Name", now=now + timedelta(hours=1))
        assert row.display_name_changed_at == changed_at
        assert row.pin_hash == "private"
        assert row.woodchuck_id == "WC-CLEANUP"
        with pytest.raises(ProfileChangeCooldown, match="again in"):
            update_profile_display_name(session, profile=row, display_name="Too Soon", now=now + timedelta(hours=23))
        update_profile_display_name(session, profile=row, display_name="Allowed", now=now + timedelta(hours=24))
        assert row.display_name == "Allowed"


def test_level_cooldown_and_independent_timestamps() -> None:
    sessions = factory()
    now = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
    with sessions() as session:
        row = profile(session)
        update_profile_display_name(session, profile=row, display_name="New Name", now=now)
        update_profile_level(session, profile=row, level="Intermediate", now=now)
        assert row.display_name_changed_at.replace(tzinfo=timezone.utc) == now
        assert row.level_changed_at.replace(tzinfo=timezone.utc) == now
        with pytest.raises(ProfileChangeCooldown):
            update_profile_level(session, profile=row, level="Advanced", now=now + timedelta(days=29))
        update_profile_level(session, profile=row, level="Advanced", now=now + timedelta(days=30))
        assert row.level == "Advanced"
        assert row.display_name_changed_at.replace(tzinfo=timezone.utc) == now


def test_streak_counts_distinct_persisted_days_and_breaks_on_gap() -> None:
    today = date(2026, 7, 29)
    assert practice_streak([today, today, today - timedelta(days=1)], today) == 2
    assert practice_streak([today - timedelta(days=1), today - timedelta(days=2)], today) == 2
    assert practice_streak([today - timedelta(days=2)], today) == 0

    sessions = factory()
    with sessions() as session:
        row = profile(session)
        session.add_all([
            PracticeChart(profile_id=row.id, practice_date=today, minutes=10, instrument="Clarinet", source="p-book", practice_details=[], credits_awarded=0),
            PracticeChart(profile_id=row.id, practice_date=today, minutes=20, instrument="Clarinet", source="p-book", practice_details=[], credits_awarded=0),
        ])
        session.commit()
        assert profile_practice_streak(session, row.id, today) == 1


def test_shed_book_and_board_cleanup_markup_and_behavior() -> None:
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    book = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    board = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")

    assert "Manage Trusted Verifiers" not in home
    assert book.index("Manage Trusted Verifiers") > book.index('id="p-book-verifier"')
    for trigger in ("woodchuck-name-value", "level-value", "instrument-object"):
        assert f'id="{trigger}"' in home
    assert "Submit P-Chart" in book
    for label in ("Copy to Clipboard", "Email Your Chart"):
        assert label not in book
    assert "Submit this P-Chart?" in book
    assert "confirmationApproved" in app_js
    assert "form.requestSubmit()" in app_js
    assert "Choose a Parent or Mentor" in app_js
    assert 'method: "POST"' in app_js and "submissionKey" in app_js
    assert "Your Position" not in board
    for title in ("Practice Minutes Leaderboard", "WEEKLY PRACTICE BY INSTRUMENT", "WEEKLY BAND CAMP POINTS"):
        assert title in board
    assert "Medal Board of Past Winners" in board
    assert "Hall of Champions" in board


def test_drafts_do_not_feed_server_streak_and_no_conflict_markers() -> None:
    app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert 'fetch("/practice-charts/streak"' in app_js
    for path in ROOT.rglob("*"):
        if path.is_file() and not {".git", ".venv", "__pycache__"}.intersection(path.parts):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line in text.splitlines():
                assert not line.startswith("<" * 7)
                assert line != "=" * 7
                assert not line.startswith(">" * 7)
