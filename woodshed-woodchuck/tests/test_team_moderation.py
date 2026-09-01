from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, contest_admin, main as main_module, teams
from app.db import Base
from app.main import app
from app.models import Season, Team, TeamMembership, TeamReport, WoodchuckProfile
from app.security import hash_pin


NOW = datetime(2026, 8, 4, 15, tzinfo=timezone.utc)


@pytest.fixture
def moderation_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (account_routes, contest_admin, main_module, teams):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setenv("CONTEST_ADMIN_TOKEN", "moderation-secret")
    with factory() as session:
        season = Season(
            key="band-camp-2026", name="Band Camp", timezone="America/Chicago",
            starts_on=date(2026, 7, 27), status="active",
        )
        reporter = WoodchuckProfile(
            woodchuck_id="WC-REPORTER", display_name="Reporter", pin_hash=hash_pin("1234"),
            instrument="Flute", level="Beginner", goal="Practice",
        )
        member = WoodchuckProfile(
            woodchuck_id="WC-MEMBER", display_name="Member", pin_hash=hash_pin("1234"),
            instrument="Trumpet", level="Beginner", goal="Practice",
        )
        session.add_all([season, reporter, member]); session.flush()
        reported = Team(
            season_id=season.id, display_name="Reported Team", normalized_name="reported team",
            emblem_key="emoji:goat", creator_profile_id=member.id,
        )
        escape = Team(
            season_id=season.id, display_name="Escape Team", normalized_name="escape team",
            emblem_key="letter:E", creator_profile_id=reporter.id,
        )
        session.add_all([reported, escape]); session.flush()
        session.add(TeamMembership(
            season_id=season.id, team_id=reported.id, profile_id=member.id,
            selected_week_start=date(2026, 8, 3), started_at=NOW,
        ))
        session.commit()
        return factory, reporter.id, member.id, reported.id, escape.id


def sign_in(client: TestClient, woodchuck_id: str) -> None:
    response = client.post("/account/login", data={
        "woodchuck_id": woodchuck_id, "pin": "1234",
    })
    assert response.status_code == 200


def test_reporting_is_authenticated_allowlisted_and_idempotent(moderation_db) -> None:
    factory, reporter_id, _member_id, team_id, _escape_id = moderation_db
    client = TestClient(app)
    assert client.post(f"/teams/{team_id}/reports", json={
        "category": "other", "details": "x",
    }).status_code == 401
    sign_in(client, "WC-REPORTER")
    assert client.post(f"/teams/{team_id}/reports", json={
        "category": "not-allowed", "details": "x",
    }).status_code == 400
    first = client.post(f"/teams/{team_id}/reports", json={
        "category": "impersonation", "details": "<script>alert(1)</script>",
    })
    second = client.post(f"/teams/{team_id}/reports", json={
        "category": "other", "details": "duplicate",
    })
    assert first.status_code == 201 and first.json()["created"] is True
    assert second.status_code == 201 and second.json()["created"] is False
    with factory() as session:
        report = session.scalar(select(TeamReport))
        assert report.reporter_profile_id == reporter_id
        assert report.details == "<script>alert(1)</script>"
        assert session.get(Team, team_id).moderation_status == "active"


def test_admin_moderation_hidden_behavior_and_restore(moderation_db) -> None:
    factory, _reporter_id, member_id, team_id, escape_id = moderation_db
    reporter_client = TestClient(app); sign_in(reporter_client, "WC-REPORTER")
    reporter_client.post(f"/teams/{team_id}/reports", json={
        "category": "inappropriate_name", "details": "Review this",
    })
    non_admin = reporter_client.post(
        f"/contests/admin/teams/{team_id}/moderation",
        data={"state": "hidden"}, follow_redirects=False,
    )
    assert non_admin.status_code == 403
    admin = TestClient(app)
    headers = {"X-Contest-Admin-Token": "moderation-secret"}
    assert admin.post(
        f"/contests/admin/teams/{team_id}/moderation",
        data={"state": "under_review"}, headers=headers, follow_redirects=False,
    ).status_code == 303
    with factory() as session:
        assert session.get(Team, team_id).moderation_status == "under_review"
    listing = reporter_client.get("/teams").json()
    assert any(row["id"] == team_id for row in listing["teams"])

    assert admin.post(
        f"/contests/admin/teams/{team_id}/moderation",
        data={"state": "hidden"}, headers=headers, follow_redirects=False,
    ).status_code == 303
    listing = reporter_client.get("/teams").json()
    assert all(row["id"] != team_id for row in listing["teams"])
    assert reporter_client.post("/teams/selection", json={"team_id": team_id}).status_code == 404
    assert reporter_client.post(f"/teams/{team_id}/reports", json={
        "category": "other", "details": "hidden",
    }).status_code == 404

    member_client = TestClient(app); sign_in(member_client, "WC-MEMBER")
    current = member_client.get("/teams").json()
    assert current["membership"]["team"]["name"] == "Hidden Team"
    assert "captain" not in current["membership"]["team"]
    assert current["membership"]["locked"] is False
    escaped = member_client.post("/teams/selection", json={"team_id": escape_id})
    assert escaped.status_code == 200 and escaped.json()["changed"] is True

    assert admin.post(
        f"/contests/admin/teams/{team_id}/moderation",
        data={"state": "active"}, headers=headers, follow_redirects=False,
    ).status_code == 303
    with factory() as session:
        team = session.get(Team, team_id)
        assert team.display_name == "Reported Team"
        assert team.emblem_key == "emoji:goat"
        report = session.scalar(select(TeamReport))
        result = admin.post(
            f"/contests/admin/team-reports/{report.id}/resolve",
            data={"action": "actioned"}, headers=headers, follow_redirects=False,
        )
        assert result.status_code == 303
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(TeamMembership).where(
            TeamMembership.profile_id == member_id
        )) == 2
        assert session.scalar(select(TeamReport)).status == "actioned"
