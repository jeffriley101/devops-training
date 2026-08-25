from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, contest_admin, main, teams
from app.db import Base
from app.main import app
from app.models import (
    ProfileCapability,
    Season,
    Team,
    TeamJoinRequest,
    TeamMembership,
    WoodchuckProfile,
)
from app.security import hash_pin


@pytest.fixture()
def director_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    for module in (account_routes, contest_admin, main, teams):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setenv("CONTEST_ADMIN_TOKEN", "director-admin-secret")
    with factory() as session:
        session.add(Season(
            key="back-to-school-2026", name="Back to School",
            timezone="America/Chicago", starts_on=date(2026, 8, 3), status="active",
        ))
        for key, name in (("DIRECTOR", "Director"), ("STUDENT", "Student"), ("OTHER", "Other Director")):
            session.add(WoodchuckProfile(
                woodchuck_id=f"WC-{key}", display_name=name,
                pin_hash=hash_pin("2468"), instrument="Flute",
                level="Beginner", goal="Practice",
            ))
        session.commit()
    yield factory


def signed_client(woodchuck_id: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/account/login", data={
        "woodchuck_id": woodchuck_id, "pin": "2468",
    }).status_code == 200
    return client


def grant_director(factory, woodchuck_id: str) -> None:
    with factory() as session:
        profile = session.scalar(select(WoodchuckProfile).where(
            WoodchuckProfile.woodchuck_id == woodchuck_id
        ))
        session.add(ProfileCapability(
            profile_id=profile.id, capability="band_director",
            granted_by="contest-admin",
        ))
        session.commit()


def test_director_capability_is_admin_controlled_and_not_self_service(director_database) -> None:
    client = signed_client("WC-DIRECTOR")
    assert client.get("/teams/director/manage", follow_redirects=False).headers["location"] == "/home"
    denied = client.post("/teams/director", json={"name": "School Band", "emblem_key": "letter:S"})
    assert denied.status_code == 403
    admin = TestClient(app)
    granted = admin.post(
        "/contests/admin/band-directors",
        headers={"X-Contest-Admin-Token": "director-admin-secret"},
        data={"woodchuck_id": "WC-DIRECTOR"},
        follow_redirects=False,
    )
    assert granted.status_code == 303
    page = client.get("/teams/director/manage")
    assert page.status_code == 200
    assert "Director Team Management" in page.text
    assert "/static/js/director-team.js?v=1" in page.text
    with director_database() as session:
        assert session.scalar(select(func.count()).select_from(ProfileCapability)) == 1


def test_private_team_join_requires_owner_approval_and_hides_from_public_list(director_database) -> None:
    grant_director(director_database, "WC-DIRECTOR")
    director = signed_client("WC-DIRECTOR")
    created = director.post("/teams/director", json={
        "name": "School Band", "emblem_key": "emoji:eagle",
    })
    assert created.status_code == 201
    team = created.json()["team"]
    assert team["visibility"] == "private" and team["director_led"] is True
    assert team["members"] == []

    student = signed_client("WC-STUDENT")
    public = student.get("/teams").json()
    assert all(row["id"] != team["id"] for row in public["teams"])
    assert student.post("/teams/selection", json={"team_id": team["id"]}).status_code == 404
    requested = student.post("/teams/private-requests", json={"join_code": team["join_code"]})
    assert requested.status_code == 201 and requested.json()["status"] == "pending"
    assert student.get("/teams").json()["pending_private_request"]["team"]["name"] == "School Band"
    with director_database() as session:
        assert session.scalar(select(func.count()).select_from(TeamMembership)) == 0

    request_id = director.get("/teams/director").json()["team"]["pending_requests"][0]["id"]
    assert student.post(
        f"/teams/director/{team['id']}/requests/{request_id}", json={"action": "approve"}
    ).status_code == 403
    approved = director.post(
        f"/teams/director/{team['id']}/requests/{request_id}", json={"action": "approve"}
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert student.get("/teams").json()["membership"]["team"]["id"] == team["id"]


def test_unrelated_director_cannot_manage_and_join_code_has_no_authority(director_database) -> None:
    grant_director(director_database, "WC-DIRECTOR")
    grant_director(director_database, "WC-OTHER")
    director = signed_client("WC-DIRECTOR")
    team = director.post("/teams/director", json={
        "name": "Private Ensemble", "emblem_key": "letter:P",
    }).json()["team"]
    student = signed_client("WC-STUDENT")
    request_id = student.post(
        "/teams/private-requests", json={"join_code": team["join_code"]}
    ).json()["request_id"]
    other = signed_client("WC-OTHER")
    assert other.post(
        f"/teams/director/{team['id']}/requests/{request_id}", json={"action": "approve"}
    ).status_code == 404
    with director_database() as session:
        row = session.get(TeamJoinRequest, request_id)
        assert row.status == "pending"


def test_reject_remove_and_director_playing_membership_are_explicit(director_database) -> None:
    grant_director(director_database, "WC-DIRECTOR")
    director = signed_client("WC-DIRECTOR")
    team = director.post("/teams/director", json={
        "name": "Concert Players", "emblem_key": "shield:blue",
    }).json()["team"]
    with director_database() as session:
        owner = session.scalar(select(WoodchuckProfile).where(
            WoodchuckProfile.woodchuck_id == "WC-DIRECTOR"
        ))
        assert session.scalar(select(TeamMembership).where(
            TeamMembership.profile_id == owner.id
        )) is None
    assert director.post(
        f"/teams/director/{team['id']}/playing-membership"
    ).status_code == 200
    managed = director.get("/teams/director").json()["team"]
    owner_id = next(row["profile_id"] for row in managed["members"] if row["display_name"] == "Director")
    assert director.delete(
        f"/teams/director/{team['id']}/members/{owner_id}"
    ).json()["removed"] is True

    student = signed_client("WC-STUDENT")
    request_id = student.post(
        "/teams/private-requests", json={"join_code": team["join_code"]}
    ).json()["request_id"]
    rejected = director.post(
        f"/teams/director/{team['id']}/requests/{request_id}", json={"action": "reject"}
    )
    assert rejected.json()["status"] == "rejected"
    assert student.get("/teams").json()["membership"]["team"] is None
