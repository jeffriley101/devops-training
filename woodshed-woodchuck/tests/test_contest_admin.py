from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import contest_admin
from app.contests import ensure_contest_definitions
from app.db import Base
from app.main import app
from app.models import (
    Contest,
    ContestResult,
    ContestWeek,
    PracticeChart,
    Season,
    WoodchuckProfile,
)


TOKEN = "test-contest-admin-token"


@pytest.fixture
def database(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Session, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(contest_admin, "SessionLocal", factory)
    monkeypatch.setenv("CONTEST_ADMIN_TOKEN", TOKEN)
    with factory() as session:
        yield session, factory


def create_season(
    session: Session,
    *,
    finalized: bool = False,
    two_weeks: bool = False,
) -> tuple[Season, list[ContestWeek]]:
    ensure_contest_definitions(session)
    season = Season(
        key="band-camp-admin",
        name="Band Camp Admin",
        timezone="America/Chicago",
        starts_on=date(2026, 7, 13),
        ends_on=date(2026, 7, 26) if two_weeks else date(2026, 7, 19),
        status="active",
    )
    session.add(season)
    session.flush()
    starts = [date(2026, 7, 13)]
    if two_weeks:
        starts.append(date(2026, 7, 20))
    weeks = []
    for week_start in starts:
        week = ContestWeek(
            season_id=season.id,
            week_start=week_start,
            week_end=week_start + timedelta(days=7),
            verification_deadline_at=datetime(
                2026, 7, 20 if week_start.day == 13 else 27, 17,
                tzinfo=timezone.utc,
            ),
            finalize_after=datetime(
                2026, 7, 20 if week_start.day == 13 else 27, 17, 5,
                tzinfo=timezone.utc,
            ),
            status="finalized" if finalized else "open",
            finalized_at=(
                datetime(2026, 7, 28, tzinfo=timezone.utc) if finalized else None
            ),
        )
        session.add(week)
        weeks.append(week)
    session.commit()
    return season, weeks


def authenticate(client: TestClient):
    return client.get(
        "/contests/admin",
        headers={"X-Contest-Admin-Token": TOKEN},
    )


def test_admin_page_requires_valid_token_and_is_not_in_student_navigation(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = database
    create_season(session)
    client = TestClient(app)

    monkeypatch.delenv("CONTEST_ADMIN_TOKEN")
    assert client.get("/contests/admin").status_code == 503
    monkeypatch.setenv("CONTEST_ADMIN_TOKEN", TOKEN)
    assert client.get("/contests/admin").status_code == 403
    assert client.get(
        "/contests/admin", headers={"X-Contest-Admin-Token": "wrong"}
    ).status_code == 403
    response = authenticate(client)

    assert response.status_code == 200
    assert "Band Camp Contest Administration" in response.text
    board = client.get("/quest").text
    shop = client.get("/store").text
    assert 'href="/contests/admin"' not in board + shop
    assert "Band Camp Standings" in board
    assert "Your Permanent Crown" in shop


def test_admin_status_is_private_and_shows_season_week_and_deadlines(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    create_season(session)
    session.add(WoodchuckProfile(
        woodchuck_id="WC-ADMIN-PRIVATE",
        display_name="Private Student Name",
        pin_hash="private-pin-hash",
        instrument="Tuba",
        level="Beginner",
        goal="Practice",
    ))
    session.commit()

    response = authenticate(TestClient(app))
    text = response.text

    assert response.status_code == 200
    for visible in (
        "Band Camp Admin", "Active Season", "Current Contest Week",
        "Verification deadline", "Finalize after", "Finalization due",
        "Rollover Readiness", "Latest Finalization Job",
    ):
        assert visible in text
    for private in (
        "Private Student Name", "WC-ADMIN-PRIVATE", "private-pin-hash",
        TOKEN, "profile_id", "verifier",
    ):
        assert private not in text


def test_finalize_current_week_action_and_immutable_repeat(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, weeks = create_season(session)
    student = WoodchuckProfile(
        woodchuck_id="WC-ADMIN-FINALIZE",
        display_name="Admin Finalize Student",
        pin_hash="hash",
        instrument="Tuba",
        level="Beginner",
        goal="Practice",
    )
    session.add(student)
    session.flush()
    session.add(PracticeChart(
        profile_id=student.id,
        practice_date=date(2026, 7, 15),
        minutes=20,
        instrument="Tuba",
        practice_details=[],
        source="p-book",
        credits_awarded=0,
    ))
    session.commit()
    client = TestClient(app)
    authenticate(client)

    response = client.post("/contests/admin/finalize-current", follow_redirects=True)
    session.expire_all()

    assert response.status_code == 200
    assert "Current contest week finalized successfully." in response.text
    assert session.get(ContestWeek, weeks[0].id).status == "finalized"
    before = list(session.scalars(select(ContestResult).order_by(ContestResult.id)))
    repeated = client.post("/contests/admin/finalize-current", follow_redirects=True)
    after = list(session.scalars(select(ContestResult).order_by(ContestResult.id)))
    assert "cannot be finalized: already_finalized" in repeated.text
    assert [(row.id, row.score, row.rank) for row in before] == [
        (row.id, row.score, row.rank) for row in after
    ]


def test_finalize_all_due_and_no_due_messages(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, weeks = create_season(session, two_weeks=True)
    client = TestClient(app)
    authenticate(client)

    first = client.post("/contests/admin/finalize-due", follow_redirects=True)
    session.expire_all()
    assert "Finalized 2 due week(s)." in first.text
    assert all(session.get(ContestWeek, week.id).status == "finalized" for week in weeks)

    repeated = client.post("/contests/admin/finalize-due", follow_redirects=True)
    assert "No contest weeks are currently due." in repeated.text


def test_finalize_current_failure_is_safe_and_does_not_leak_details(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = database
    _, weeks = create_season(session)
    client = TestClient(app)
    authenticate(client)

    def fail(*args: object, **kwargs: object):
        raise RuntimeError("student@example.com PIN=1234 private note")

    monkeypatch.setattr(contest_admin, "finalize_contest_week", fail)
    response = client.post(
        "/contests/admin/finalize-current", follow_redirects=True
    )
    session.expire_all()

    assert "failed without partial changes" in response.text
    assert "student@example.com" not in response.text
    assert "1234" not in response.text
    assert session.get(ContestWeek, weeks[0].id).status == "open"


def test_rollover_readiness_blocked_and_confirmation_required(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    source, _ = create_season(session)
    client = TestClient(app)
    authenticate(client)

    readiness = client.post("/contests/admin/readiness", follow_redirects=True)
    assert "Rollover blocked" in readiness.text
    assert "unfinalized_contest_weeks" in readiness.text
    rollover = client.post(
        "/contests/admin/rollover",
        data={
            "source_key": source.key,
            "next_key": "band-camp-next",
            "next_name": "Band Camp Next",
            "next_start": "2026-07-20",
            "next_end": "2026-08-02",
            "confirmation": "yes",
        },
        follow_redirects=True,
    )
    assert "must exactly match" in rollover.text
    assert session.scalar(select(Season).where(Season.key == "band-camp-next")) is None


def test_successful_rollover_requires_explicit_configuration(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    source, _ = create_season(session, finalized=True)
    client = TestClient(app)
    authenticate(client)

    response = client.post(
        "/contests/admin/rollover",
        data={
            "source_key": source.key,
            "next_key": "band-camp-next",
            "next_name": "Band Camp Next",
            "next_start": "2026-07-20",
            "next_end": "2026-08-02",
            "confirmation": "ROLL OVER",
        },
        follow_redirects=True,
    )
    session.expire_all()

    assert response.status_code == 200
    assert "Rollover complete" in response.text
    assert session.get(Season, source.id).status == "closed"
    next_season = session.scalar(select(Season).where(Season.key == "band-camp-next"))
    assert next_season is not None and next_season.status == "active"
    assert session.scalar(select(func.count()).select_from(ContestWeek).where(
        ContestWeek.season_id == next_season.id
    )) == 2


def test_invalid_rollover_configuration_is_rejected_without_changes(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    source, _ = create_season(session, finalized=True)
    client = TestClient(app)
    authenticate(client)

    response = client.post(
        "/contests/admin/rollover",
        data={
            "source_key": source.key,
            "next_key": "band-camp-invalid",
            "next_name": "Invalid Season",
            "next_start": "2026-07-21",
            "next_end": "2026-08-02",
            "confirmation": "ROLL OVER",
        },
        follow_redirects=True,
    )
    session.expire_all()

    assert "Rollover blocked" in response.text
    assert "must start on Monday" in response.text
    assert session.get(Season, source.id).status == "active"
    assert session.scalar(select(Season).where(
        Season.key == "band-camp-invalid"
    )) is None


def test_mutation_routes_are_post_only(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    create_season(session)
    client = TestClient(app)
    authenticate(client)

    for path in (
        "/contests/admin/finalize-current",
        "/contests/admin/finalize-due",
        "/contests/admin/readiness",
        "/contests/admin/rollover",
    ):
        assert client.get(path).status_code == 405


def test_admin_mobile_css_uses_cards_without_wide_tables() -> None:
    root = Path(__file__).resolve().parents[1]
    markup = (root / "templates" / "contest_admin.html").read_text(encoding="utf-8")
    css = (root / "static" / "css" / "styles.css").read_text(encoding="utf-8")

    assert "<table" not in markup
    assert "contest-admin-card" in markup
    assert "@media (max-width: 600px)" in css
    assert "min-height: 44px" in css
    assert "grid-template-columns: 1fr" in css
