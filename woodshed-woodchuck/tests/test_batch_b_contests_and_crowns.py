from datetime import date, datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app
from app.models import PracticeChart, WoodchuckProfile
from app.practice_chart_routes import format_practice_minutes, practice_totals_payload


ROOT = Path(__file__).resolve().parents[1]


def test_contest_opt_in_migration_defaults_existing_chart_true(tmp_path, monkeypatch) -> None:
    database = tmp_path / "contest-opt-in.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "a21c4e7d9b32")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO woodchuck_profiles
              (id, woodchuck_id, display_name, pin_hash, instrument, level, goal,
               created_at, updated_at, display_name_changed_at, level_changed_at)
            VALUES (1, 'WC-OLD-CHART', 'Old Chart', 'private', 'Flute', 'Beginner',
                    'Practice', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL)
        """))
        connection.execute(text("""
            INSERT INTO practice_charts
              (id, profile_id, practice_date, minutes, instrument, note,
               practice_details, source, credits_awarded, created_at, updated_at,
               submission_key)
            VALUES (1, 1, '2026-07-29', 30, 'Flute', NULL, '[]', 'p-book', 0,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'old-chart')
        """))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT include_contests FROM practice_charts WHERE id = 1"
        )) == 1


def test_server_practice_totals_use_central_week_and_include_opt_out() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        profile = WoodchuckProfile(
            woodchuck_id="WC-TOTALS", display_name="Totals", pin_hash="private",
            instrument="Tuba", level="Beginner", goal="Practice",
        )
        session.add(profile)
        session.flush()
        session.add_all([
            PracticeChart(profile_id=profile.id, practice_date=date(2026, 7, 27), minutes=42, instrument="Tuba", practice_details=[], source="p-book", credits_awarded=0, include_contests=True),
            PracticeChart(profile_id=profile.id, practice_date=date(2026, 7, 29), minutes=75, instrument="Tuba", practice_details=[], source="p-book", credits_awarded=0, include_contests=False),
            PracticeChart(profile_id=profile.id, practice_date=date(2026, 7, 20), minutes=90, instrument="Tuba", practice_details=[], source="p-book", credits_awarded=0, include_contests=True),
            PracticeChart(profile_id=profile.id, practice_date=date(2026, 7, 29), minutes=0, instrument="Tuba", practice_details=[], source="p-book", credits_awarded=0, include_contests=True),
        ])
        session.commit()
        payload = practice_totals_payload(session, profile.id, today=date(2026, 7, 29))
    assert payload == {
        "week_start": "2026-07-27", "week_end": "2026-08-02",
        "this_week_minutes": 117, "this_week_display": "1 hour 57 minutes",
        "career_minutes": 207, "career_display": "3 hours 27 minutes",
    }
    assert format_practice_minutes(42) == "42 minutes"
    assert format_practice_minutes(195) == "3 hours 15 minutes"
    assert not {"profile_id", "account_id", "email", "note", "verifier"}.intersection(payload)


def test_practice_totals_endpoint_requires_authentication() -> None:
    response = TestClient(app).get("/practice-charts/totals")

    assert response.status_code == 401


def test_batch_b_markup_and_privacy_hooks() -> None:
    book = (ROOT / "templates/p_book.html").read_text(encoding="utf-8")
    board = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
    shop = (ROOT / "templates/store.html").read_text(encoding="utf-8")
    javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert 'id="p-book-include-contests"' in book and "checked" in book
    assert "Include this chart in Band Camp contests" in book
    assert "include_contests: includeContests" in javascript
    assert "Rehearsal / Lesson" in board
    assert "Band Camp Hours Bonus" not in board
    assert "Actual practice time comes from submitted P-Charts" in board
    assert "Weekly Gold, Silver, and Bronze winners" in board
    assert "Medals will appear after a Band Camp week is finalized." in board
    assert "This Week’s Practice" in book and "Career Practice" in book
    for category in (
        "Practice Crown", "Band Camp Crown", "Trivia Crown",
        "Instrument Care Crown", "Marching Crown", "Band Camp Hours Crown",
    ):
        assert category in (ROOT / "app/contests.py").read_text(encoding="utf-8")
    assert "crown-category-list" in shop


def test_no_conflict_markers_after_batch_b() -> None:
    for path in ROOT.rglob("*"):
        if not path.is_file() or {".git", ".venv", "__pycache__"}.intersection(path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        assert not any(line.startswith("<" * 7) or line == "=" * 7 or line.startswith(">" * 7) for line in lines)
