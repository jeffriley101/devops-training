"""Focused HTTP checks for individually shared charts."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import classroom, classroom_chart_routes, classroom_reporting, classroom_s2, main, practice_chart_routes
from app.classroom_models import ClassroomChartDirectorShare, ClassroomTeachingAssignment
from app.models import PracticeChart, WoodchuckProfile
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import NOW, join, s2_db
from test_classroom_director import web, login, confirm


@pytest.fixture
def sharing_web(web, monkeypatch):
    monkeypatch.setattr(classroom_chart_routes, "SessionLocal", web)
    monkeypatch.setattr(practice_chart_routes, "SessionLocal", web)
    monkeypatch.setattr(classroom_reporting, "_now", lambda: NOW)
    with web() as session:
        join(session, visible="FIRST2026")
        assignment = session.scalar(select(ClassroomTeachingAssignment).where(
            ClassroomTeachingAssignment.class_id == web.ids[0], ClassroomTeachingAssignment.verifier_id == 2))
        web.assignment_id = assignment.id
        session.commit()
    return web


def test_submit_edit_and_director_view(sharing_web):
    client = TestClient(main.app, headers={"Origin": "http://testserver"})
    assert client.post("/account/login", data={"woodchuck_id": "WC-SYNTHETIC", "pin": "2468"}).status_code == 200
    options = client.get("/classroom/chart-sharing").json()
    assert [c["assignment_id"] for c in options["choices"]] == [sharing_web.assignment_id]
    data = {"practice_date": datetime.now(ZoneInfo("America/Chicago")).date().isoformat(),
            "minutes": 12, "note": "synthetic private chart", "practice_details": ["Scales"],
            "submission_key": "synthetic-share-test", "include_contests": False,
            "include_team_contests": False, "classroom_assignment_ids": [sharing_web.assignment_id]}
    response = client.post("/practice-charts", json=data)
    assert response.status_code == 201, response.text
    chart_id = response.json()["chart"]["id"]
    assert client.get(f"/classroom/chart-sharing/{chart_id}").json()["selected"] == [sharing_web.assignment_id]
    director = login(2)
    path = f"/classroom/director/1/classes/{sharing_web.ids[0]}"
    assert "synthetic private chart" in confirm(director, path).text
    response = client.post(f"/classroom/chart-sharing/{chart_id}",
                           json={"csrf": options["csrf"], "assignment_ids": []})
    assert response.status_code == 200, response.text
    assert "synthetic private chart" not in confirm(director, path).text
    with sharing_web() as session:
        saved = session.get(PracticeChart, chart_id)
        assert (saved.minutes, saved.note, saved.practice_details) == (12, "synthetic private chart", ["Scales"])
        assert session.scalar(select(ClassroomChartDirectorShare.withdrawn_at)) is not None


def test_gate_off_and_cross_student(sharing_web, monkeypatch):
    client = TestClient(main.app, headers={"Origin": "http://testserver"})
    assert client.post("/account/login", data={"woodchuck_id": "WC-SYNTHETIC", "pin": "2468"}).status_code == 200
    with sharing_web() as session:
        session.add(WoodchuckProfile(id=2, woodchuck_id="WC-OTHER", display_name="Other synthetic student",
            pin_hash="synthetic", instrument="Flute", level="Beginner", goal="Practice"))
        session.flush()
        chart = PracticeChart(profile_id=2, practice_date=NOW.date(), minutes=11, instrument="Flute")
        session.add(chart)
        session.commit()
        other_chart = chart.id
    options = client.get("/classroom/chart-sharing").json()
    assert client.get(f"/classroom/chart-sharing/{other_chart}").status_code == 404
    assert client.post(f"/classroom/chart-sharing/{other_chart}",
        json={"csrf": options["csrf"], "assignment_ids": [sharing_web.assignment_id]}).status_code == 404
    monkeypatch.delenv("CLASSROOM_DIRECTOR_ENABLED")
    assert client.get("/classroom/chart-sharing").status_code == 404
    response = client.post("/practice-charts", json={
        "practice_date": datetime.now(ZoneInfo("America/Chicago")).date().isoformat(),
        "minutes": 5, "submission_key": "sharing-gate-off", "include_contests": False,
        "include_team_contests": False, "classroom_assignment_ids": [sharing_web.assignment_id]})
    assert response.status_code == 400
    with sharing_web() as session:
        assert session.scalar(select(PracticeChart.id).where(PracticeChart.submission_key == "sharing-gate-off")) is None


def test_multiple_classes_select_only_one_recipient_and_private_default(sharing_web):
    with sharing_web() as session:
        join(session, visible="SECOND2026", program_id=2)
        second = classroom_s2.assign_teacher(session, actor=actor(session), program_id=2,
                                             class_id=sharing_web.ids[1], verifier_id=3)
        second_id = second.id
        session.commit()
    from app import classroom_chart_sharing
    from test_classroom_s2 import student
    with sharing_web() as session:
        assert len(classroom_chart_sharing.choices(session, student(session))) == 2
    client = TestClient(main.app, headers={"Origin": "http://testserver"})
    assert client.post("/account/login", data={"woodchuck_id": "WC-SYNTHETIC", "pin": "2468"}).status_code == 200
    options = client.get("/classroom/chart-sharing").json()
    assert {item["assignment_id"] for item in options["choices"]} == {sharing_web.assignment_id, second_id}
    data = {"practice_date": datetime.now(ZoneInfo("America/Chicago")).date().isoformat(),
            "minutes": 5, "note": "private until selected", "submission_key": "synthetic-private",
            "include_contests": False, "include_team_contests": False}
    response = client.post("/practice-charts", json=data)
    assert response.status_code == 201, response.text
    chart_id = response.json()["chart"]["id"]
    assert client.get(f"/classroom/chart-sharing/{chart_id}").json()["selected"] == []
    response = client.post(f"/classroom/chart-sharing/{chart_id}",
                           json={"csrf": options["csrf"], "assignment_ids": [second_id]})
    assert response.status_code == 200, response.text
    assert "private until selected" not in confirm(login(2), f"/classroom/director/1/classes/{sharing_web.ids[0]}").text
    assert "private until selected" in confirm(login(3), f"/classroom/director/2/classes/{sharing_web.ids[1]}").text
    # Switching Programs requires a separate revocation before the new grant.
    url = f"/classroom/chart-sharing/{chart_id}"
    assert client.post(url, json={"csrf": options["csrf"], "assignment_ids": [sharing_web.assignment_id]}).status_code == 404
    assert client.post(url, json={"csrf": options["csrf"], "assignment_ids": []}).status_code == 200
    assert client.post(url, json={"csrf": options["csrf"], "assignment_ids": [sharing_web.assignment_id]}).status_code == 200
    assert "private until selected" in confirm(login(2), f"/classroom/director/1/classes/{sharing_web.ids[0]}").text
    assert "private until selected" not in confirm(login(3), f"/classroom/director/2/classes/{sharing_web.ids[1]}").text
