"""Disposable HTTP checks of persistent earning attribution and explicit leave."""
from datetime import date, datetime, timedelta, timezone
import sys

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import contests, main, practice_chart_routes, teams
from app.db import Base
from app.models import (CampPointAward, PersistentTeamControl, Team, TeamMembership,
                        TeamMembershipTransition, TeamReport, PracticeChart)
from app.seasons import bootstrap_canonical_seasons
from tests.team_factory import make_team
from tests.test_arcade_economy import signed_client
from tests.test_team_families import disposable_url


NOW = datetime(2026, 10, 1, 18, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(params=["sqlite", "postgresql"])
def http_db(request, tmp_path, monkeypatch):
    engine = (create_engine("sqlite://", poolclass=StaticPool,
                            connect_args={"check_same_thread": False})
              if request.param == "sqlite" else create_engine(disposable_url(tmp_path, request.param)))
    if request.param == "sqlite":
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    for module in (contests, practice_chart_routes, teams):
        monkeypatch.setattr(module, "datetime", Clock)
    from app import team_authority
    monkeypatch.setattr(team_authority, "datetime", Clock)
    client, profile = signed_client(factory, "PT")
    with factory() as session:
        bootstrap_canonical_seasons(session)
        from app.models import Season
        origin = session.scalar(select(Season).where(Season.key == "back-to-school-2026"))
        old = session.scalar(select(Season).where(Season.key == "band-camp-2026"))
        operating = []
        for index, name in enumerate(("Eureka", "Union", "The Teachers", "St. Louis", "Mr. Pickles Minions")):
            team = make_team(session, id=10 + index, season_id=origin.id,
                             display_name=name, normalized_name=name.casefold(),
                             emblem_key=f"letter:{chr(65 + index)}", is_operating=True)
            session.add(team)
            operating.append(team)
        session.flush()
        session.add(TeamMembership(profile_id=profile.id, team_id=10, season_id=origin.id,
                    started_at=datetime(2026, 9, 14, 5, tzinfo=timezone.utc),
                    selected_week_start=date(2026, 9, 14), is_persistent=True))
        historic = make_team(session, season_id=old.id, display_name="Historical Team",
                            normalized_name="historical team", emblem_key="emoji:cat")
        session.add(historic)
        session.flush()
        session.add(TeamMembership(profile_id=profile.id, team_id=historic.id, season_id=old.id,
                    started_at=datetime(2026, 8, 1, 5, tzinfo=timezone.utc),
                    selected_week_start=date(2026, 7, 27)))
        session.add(PersistentTeamControl(id=1, activated_at=NOW - timedelta(days=1),
                    rules_from_week_start=date(2026, 10, 5)))
        session.add(TeamReport(team_id=10, reporter_profile_id=profile.id,
                              category="other", details="WHY CAN'T I LEAVE"))
        null_types = ["care", "hours", "hours", "marching"] + ["trivia"] * 5
        for index, activity in enumerate(null_types):
            session.add(CampPointAward(profile_id=profile.id, activity_type=activity,
                        points_awarded=1, occurred_at=NOW - timedelta(days=1),
                        duplicate_key=f"historical-null:{index}", team_id=None))
        for index in range(14):
            session.add(CampPointAward(profile_id=profile.id, activity_type="contest-placement",
                        points_awarded=3, occurred_at=NOW - timedelta(days=1),
                        duplicate_key=f"historical-placement:{index}", team_id=10))
        session.commit()
    yield factory, client, profile
    engine.dispose()


def old_awards(factory):
    with factory() as session:
        return [dict(row) for row in session.execute(select(CampPointAward.__table__).where(
            CampPointAward.duplicate_key.like("historical-%"))).mappings()]


@pytest.mark.parametrize("activity", ["hours", "care", "marching", "trivia"])
@pytest.mark.parametrize("member", [True, False])
def test_board_new_earning_uses_effective_authority_without_backfill(http_db, activity, member):
    factory, client, profile = http_db
    before = old_awards(factory)
    if not member:
        with factory() as session:
            row = session.scalar(select(TeamMembership).where(TeamMembership.is_persistent.is_(True)))
            row.ended_at = NOW - timedelta(seconds=1)
            session.commit()
    if activity == "trivia":
        question = contests.trivia_question_for(NOW.astimezone(contests.CENTRAL).date())
        response = client.post("/contests/trivia/answer", json={
            "activity_date": "2026-10-01", "selected_answer_id": question["correct_answer_id"]})
    else:
        response = client.post("/contests/camp-points/awards", json={
            "activity_date": "2026-10-01", "activity_type": activity})
    assert response.status_code == 200, response.text
    with factory() as session:
        new = session.scalars(select(CampPointAward).where(
            CampPointAward.profile_id == profile.id,
            ~CampPointAward.duplicate_key.like("historical-%"))).all()
        assert len(new) == 1
        assert new[0].team_id == (10 if member else None)
    assert old_awards(factory) == before
    assert sum(row["team_id"] is None for row in before) == 9


@pytest.mark.parametrize("member", [True, False])
def test_book_and_pristine_snapshot_authority(http_db, member):
    factory, client, profile = http_db
    if not member:
        with factory() as session:
            row = session.scalar(select(TeamMembership).where(TeamMembership.is_persistent.is_(True)))
            row.ended_at = NOW - timedelta(seconds=1)
            session.commit()
    result = client.post("/practice-charts", json={"practice_date": "2026-10-01",
        "minutes": 10, "submission_key": "persistent-book", "include_team_contests": True})
    assert result.status_code == 201, result.text
    pristine = client.post("/practice-charts/pristine", json={"detected_playing_seconds": 60,
        "submission_key": "persistent-pristine", "include_team_contests": True})
    assert pristine.status_code == 201, pristine.text
    with factory() as session:
        rows = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == profile.id)).all()
        assert len(rows) == 2
        book = next(row for row in rows if row.source == "p-book")
        assert book.team_id == (10 if member else None)
        pristine_row = next(row for row in rows if row.source == "pristine")
        # Pristine remains private, non-competitive evidence under SEC-003.
        assert pristine_row.team_id is None and pristine_row.include_team_contests is False


def test_leave_http_preserves_history_and_correction_cannot_be_replenished(http_db):
    factory, client, profile = http_db
    before = old_awards(factory)
    payload = client.get("/teams").json()
    assert payload["membership"]["team"]["id"] == 10
    assert payload["membership"]["leave_available"] is True
    assert len(payload["teams"]) == 5
    response = client.delete("/teams/selection")
    assert response.status_code == 200, response.text
    assert client.get("/teams").json()["membership"]["team"] is None
    assert client.delete("/teams/selection").status_code == 200  # retry is a no-op
    assert client.post("/teams/selection", json={"team_id": 11}).status_code == 409
    with factory() as session:
        transitions = session.scalars(select(TeamMembershipTransition)).all()
        assert len(transitions) == 1 and transitions[0].action == "leave"
        report = session.scalar(select(TeamReport))
        assert report.team_id == 10 and report.details == "WHY CAN'T I LEAVE"
        assert len(session.scalars(select(Team).where(Team.is_operating.is_(True))).all()) == 5
    assert old_awards(factory) == before


def test_leave_control_is_rendered_for_signed_in_student(http_db):
    _, client, _ = http_db
    page = client.get("/home")
    assert page.status_code == 200
    assert 'id="shed-team-leave"' in page.text
