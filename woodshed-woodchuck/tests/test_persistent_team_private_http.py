"""Future private/director Teams use the same persistent authority and codes."""
from sqlalchemy import select

from app.models import ProfileCapability, Season, Team, TeamJoinRequest, TeamMembership
from tests.test_arcade_economy import signed_client
from tests.test_persistent_team_http import http_db


def test_private_request_approval_and_removal_ignore_origin_season(http_db):
    factory, student_client, student = http_db
    director_client, director = signed_client(factory, "P-DIR")
    with factory() as session:
        session.add(ProfileCapability(profile_id=director.id, capability="band_director"))
        session.commit()
    created = director_client.post("/teams/director", json={"name": "Director Class", "emblem_key": "emoji:dog"})
    assert created.status_code == 201, created.text
    team_id = created.json()["team"]["id"]
    code = created.json()["team"]["join_code"]
    with factory() as session:
        team = session.get(Team, team_id)
        assert team.is_operating and team.season_id is None
        # No active theme is necessary to manage or join a continuing Class.
        for season in session.scalars(select(Season)):
            season.status = "closed"
        session.commit()
    assert student_client.post("/teams/selection", json={"team_id": team_id}).status_code == 404
    submitted = student_client.post("/teams/private-requests", json={"join_code": code})
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["request_id"]
    with factory() as session:
        row = session.get(TeamJoinRequest, request_id)
        assert row.is_persistent and row.season_id is None
    repeated = student_client.post("/teams/private-requests", json={"join_code": code})
    assert repeated.json()["created"] is False
    assert student_client.post(f"/teams/director/{team_id}/requests/{request_id}", json={"action": "approve"}).status_code == 403
    approved = director_client.post(f"/teams/director/{team_id}/requests/{request_id}", json={"action": "approve"})
    assert approved.status_code == 200, approved.text
    payload = director_client.get("/teams/director", params={"team_id": team_id})
    assert payload.status_code == 200, payload.text
    assert payload.json()["team"]["join_code"] == code
    assert [row["profile_id"] for row in payload.json()["team"]["members"]] == [student.id]
    removed = director_client.delete(f"/teams/director/{team_id}/members/{student.id}")
    assert removed.status_code == 200, removed.text
    with factory() as session:
        active = session.scalars(select(TeamMembership).where(
            TeamMembership.profile_id == student.id, TeamMembership.is_persistent.is_(True),
            TeamMembership.ended_at.is_(None))).all()
        assert not active
    # Removal does not erase the student's correction used by approval.
    assert student_client.post("/teams/selection", json={"team_id": 10}).status_code == 409
