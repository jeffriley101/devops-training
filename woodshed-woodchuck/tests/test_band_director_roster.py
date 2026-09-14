from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.team_factory import make_team

from app import main, verifier_routes
from app.db import Base
from app.models import (
    PracticeChart, PracticeChartVerification,
    Season, Team, TeamMembership, TeamWeekMembershipSnapshot, Contest, ContestWeek, ContestResult,
    StudentVerifierConnection, TrustedVerifier, TrustedVerifierInvitation,
    WoodchuckProfile,
)
from app.security import hash_pin
from app.verifiers import band_director_students
from app.band_director_practice import band_director_practice_students


@pytest.fixture
def roster_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(verifier_routes, "SessionLocal", factory)
    with factory() as session:
        session.add_all([
            TrustedVerifier(id=1, email="director@example.com", display_name="Director One",
                            pin_hash=hash_pin("2468")),
            TrustedVerifier(id=2, email="other@example.com", display_name="Director Two",
                            pin_hash=hash_pin("2468")),
        ])
        session.commit()
    yield factory
    engine.dispose()


def add_student(factory, name, *, role="band_director", status="accepted",
                verifier_id=1, active=True, connected=True):
    with factory() as session:
        student = WoodchuckProfile(
            woodchuck_id=f"WC-{name}", display_name=name, pin_hash="private-pin-hash",
            instrument="Trumpet", level="Beginner", goal="Build daily consistency",
            status="active" if active else "deleted",
        )
        session.add(student)
        session.flush()
        if connected:
            session.add(StudentVerifierConnection(
                profile_id=student.id, verifier_id=verifier_id, role=role, status=status,
            ))
        session.commit()
        return student.id


def signed_client(email="director@example.com"):
    client = TestClient(main.app)
    assert client.post("/trusted-verifiers/login", data={
        "email": email, "pin": "2468",
    }).status_code == 200
    return client


def test_accepted_director_roster_and_safe_basics(roster_db):
    add_student(roster_db, "Musician Alpha")
    add_student(roster_db, "Musician Beta")
    response = signed_client().get("/band-director/dashboard")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    for text in ("Band Director Dashboard", "Director One",
                 "Musician Alpha", "Musician Beta", "PROGRAM RATING"):
        assert text in response.text
    assert '<h1>Band Director Dashboard</h1>' in response.text
    assert 'class="ww-dashboard-header"' in response.text
    assert "WC-Musician" not in response.text
    assert "private-pin-hash" not in response.text
    with roster_db() as session:
        rows = band_director_students(session, verifier_id=1)
    assert len(rows) == 2
    assert set(rows[0]) == {"profile_id", "display_name", "instrument", "level", "goal"}


@pytest.mark.parametrize("role", ["parent", "mentor", "guardian", "private_teacher", "coach", "other_trusted_adult"])
def test_other_roles_are_excluded_for_same_verifier(roster_db, role):
    add_student(roster_db, "Visible Musician")
    add_student(roster_db, "Excluded Musician", role=role)
    html = signed_client().get("/band-director/dashboard").text
    assert "Visible Musician" in html
    assert "Excluded Musician" not in html


@pytest.mark.parametrize("status", ["pending", "rejected", "disconnected"])
def test_unaccepted_connections_are_excluded(roster_db, status):
    add_student(roster_db, "Excluded Musician", status=status)
    html = signed_client().get("/band-director/dashboard").text
    assert "Excluded Musician" not in html
    assert "No Band Director students are connected yet." in html


def test_verifiers_cannot_select_another_roster(roster_db):
    add_student(roster_db, "First Musician")
    add_student(roster_db, "Second Musician", verifier_id=2)
    first = signed_client().get("/band-director/dashboard?verifier_id=2")
    second = signed_client("other@example.com").get("/band-director/dashboard")
    assert "First Musician" in first.text and "Second Musician" not in first.text
    assert "Second Musician" in second.text and "First Musician" not in second.text


def test_invitations_and_deleted_students_do_not_grant_access(roster_db):
    add_student(roster_db, "Deleted Musician", active=False)
    for status in ("pending", "rejected"):
        student_id = add_student(roster_db, f"Invitation {status}", connected=False)
        with roster_db() as session:
            session.add(TrustedVerifierInvitation(
                profile_id=student_id, email="director@example.com", role="band_director",
                token_hash=status, status=status,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            ))
            session.commit()
    html = signed_client().get("/band-director/dashboard").text
    assert "Deleted Musician" not in html
    assert "Invitation pending" not in html
    assert "Invitation rejected" not in html
    assert "No Band Director students are connected yet." in html


def test_empty_roster(roster_db):
    response = signed_client().get("/band-director/dashboard")
    assert response.status_code == 200
    assert "No Band Director students are connected yet." in response.text
    assert "after you accept their invitations" in response.text


def test_unauthenticated_and_stale_session_redirect_like_verifier_dashboard(roster_db):
    with TestClient(main.app) as client:
        response = client.get("/band-director/dashboard", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/trusted-verifiers/login"
    client = signed_client()
    with roster_db() as session:
        session.delete(session.get(TrustedVerifier, 1))
        session.commit()
    response = client.get("/band-director/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/trusted-verifiers/login"


def test_profile_capability_alone_does_not_authorize_adult_dashboard(roster_db):
    from app.models import ProfileCapability
    from itsdangerous import TimestampSigner
    import base64
    import json

    profile_id = add_student(roster_db, "Unrelated Player", connected=False)
    with roster_db() as session:
        session.add(ProfileCapability(profile_id=profile_id, capability="band_director"))
        session.commit()
    client = TestClient(main.app)
    cookie = TimestampSigner(main.SESSION_SECRET).sign(base64.b64encode(json.dumps({
        "woodchuck_profile_id": profile_id, "woodchuck_session_version": 0,
    }).encode())).decode()
    client.cookies.set("session", cookie)
    response = client.get("/band-director/dashboard", follow_redirects=False)
    assert response.status_code == 303
    client.post("/trusted-verifiers/login", data={"email": "director@example.com", "pin": "2468"})
    response = client.get("/band-director/dashboard")
    assert response.status_code == 200
    assert "Unrelated Player" not in response.text


def test_roster_escapes_student_content(roster_db):
    add_student(roster_db, "<script>alert(1)</script>")
    html = signed_client().get("/band-director/dashboard").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def add_chart(factory, profile_id, practice_date, minutes, *, submitted=None,
              status=None, reviewer=1, note=None, include_contests=True):
    with factory() as session:
        chart = PracticeChart(
            profile_id=profile_id, practice_date=practice_date, minutes=minutes,
            instrument="Trumpet", created_at=submitted or datetime(2026, 9, 9, 18, tzinfo=timezone.utc),
            include_contests=include_contests,
        )
        session.add(chart)
        session.flush()
        if status:
            session.add(PracticeChartVerification(
                practice_chart_id=chart.id, verifier_id=reviewer,
                status=status, response_note=note,
            ))
        session.commit()


def test_week_totals_use_practice_date_and_include_all_persisted_practice(roster_db):
    first = add_student(roster_db, "Alpha")
    second = add_student(roster_db, "Beta")
    for day, minutes in [(6, 100), (7, 10), (13, 20), (14, 200)]:
        add_chart(roster_db, first, date(2026, 9, day), minutes, include_contests=False)
    add_chart(roster_db, second, date(2026, 9, 8), 45, status="rejected")
    with roster_db() as session:
        rows = band_director_practice_students(session, verifier_id=1, today=date(2026, 9, 9))
    assert [row["this_week_minutes"] for row in rows] == [30, 45]
    assert all(row["week_start"] == "2026-09-07" and row["week_end"] == "2026-09-13" for row in rows)
    assert all("profile_id" not in row for row in rows)
    assert all(chart["minutes"] != 45 for chart in rows[0]["recent_charts"])
    assert [chart["minutes"] for chart in rows[1]["recent_charts"]] == [45]


def test_recent_charts_are_five_latest_submissions_with_stable_ties(roster_db):
    student_id = add_student(roster_db, "Recent")
    for minutes in range(1, 8):
        add_chart(roster_db, student_id, date(2026, 9, 15 - minutes), minutes,
                  submitted=datetime(2026, 9, 9, minutes, tzinfo=timezone.utc))
    add_chart(roster_db, student_id, date(2026, 9, 1), 8,
              submitted=datetime(2026, 9, 9, 7, tzinfo=timezone.utc))
    with roster_db() as session:
        charts = band_director_practice_students(session, verifier_id=1)[0]["recent_charts"]
    assert [chart["minutes"] for chart in charts] == [8, 7, 6, 5, 4]


def test_condensed_dashboard_hides_chart_details_and_private_notes(roster_db):
    student_id = add_student(roster_db, "Reviews")
    for status in ("pending", "approved", "rejected"):
        add_chart(roster_db, student_id, date(2026, 9, 9), 25,
                  status=status, note="Private verifier note")
    response = signed_client().get("/band-director/dashboard")
    assert response.status_code == 200
    assert "Private verifier note" not in response.text
    assert "Recent P-Charts" not in response.text
    assert "data-band-director-review" not in response.text
    assert 'href="/trusted-verifiers/dashboard"' in response.text


@pytest.mark.parametrize("kwargs", [
    {"verifier_id": 2}, {"role": "parent"}, {"role": "guardian"},
    {"role": "private_teacher"}, {"status": "pending"}, {"status": "rejected"},
    {"active": False},
])
def test_unauthorized_practice_cannot_be_requested(roster_db, kwargs):
    allowed_id = add_student(roster_db, "Allowed")
    hidden_id = add_student(roster_db, "Hidden", **kwargs)
    add_chart(roster_db, allowed_id, date(2026, 9, 9), 15)
    add_chart(roster_db, hidden_id, date(2026, 9, 9), 987,
              status="approved", note="Confidential review")
    response = signed_client().get(f"/band-director/dashboard?profile_id={hidden_id}&verifier_id=2")
    assert response.status_code == 200
    assert "Allowed" in response.text
    assert "Hidden" not in response.text
    assert "987 minutes" not in response.text
    assert "Confidential review" not in response.text


def test_student_without_practice_has_clean_empty_states(roster_db):
    add_student(roster_db, "New Musician")
    html = signed_client().get("/band-director/dashboard").text
    assert '<td data-sort-value="0">0</td>' in html
    assert "New Musician" in html
    assert "No team" in html


def test_current_date_is_determined_in_central_time(roster_db, monkeypatch):
    from app import band_director_practice, band_director_dashboard

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 14, 2, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(band_director_practice, "datetime", FixedDatetime)
    monkeypatch.setattr(band_director_dashboard, "datetime", FixedDatetime)
    student_id = add_student(roster_db, "Sunday")
    add_chart(roster_db, student_id, date(2026, 9, 13), 20)
    add_chart(roster_db, student_id, date(2026, 9, 14), 50)
    response = signed_client().get("/band-director/dashboard")
    assert response.context["students"][0]["weekly"]["total"] == 20
    assert "Sep 7–13, 2026" in response.text


@pytest.fixture
def contest_roster(roster_db, monkeypatch):
    from app import band_director_practice, band_director_dashboard
    from app.contests import contest_week_schedule

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 9, 18, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(band_director_practice, "datetime", FixedDatetime)
    monkeypatch.setattr(band_director_dashboard, "datetime", FixedDatetime)
    end, deadline, finalizes = contest_week_schedule(date(2026, 9, 7))
    with roster_db() as session:
        season = Season(key="back-to-school-2026", name="School", status="active",
                        starts_on=date(2026, 8, 3))
        old = Season(key="band-camp-2026", name="Old", status="closed",
                     starts_on=date(2026, 7, 27), ends_on=date(2026, 8, 2))
        session.add_all([season, old])
        session.flush()
        session.add(ContestWeek(season_id=season.id, week_start=date(2026, 9, 7),
                               week_end=end, verification_deadline_at=deadline,
                               finalize_after=finalizes, status="open"))
        for name, owner, emblem in [("Current Team", season, "emoji:goat"),
                                     ("Old Team", old, "letter:A"),
                                     ("Snapshot Team", season, "shield:blue")]:
            session.add(make_team(session, season_id=owner.id, display_name=name,
                             normalized_name=name.lower(), emblem_key=emblem))
        session.commit()
    return roster_db


def join_roster_team(factory, profile_id, name):
    from sqlalchemy import select
    with factory() as session:
        team = session.scalar(select(Team).where(Team.display_name == name))
        session.add(TeamMembership(profile_id=profile_id, team_id=team.id,
                                   season_id=team.season_id, selected_week_start=date(2026, 9, 7),
                                   started_at=datetime(2026, 9, 7, 12, tzinfo=timezone.utc)))
        session.commit()


def test_current_season_team_and_emblem_without_old_team_leak(contest_roster):
    first = add_student(contest_roster, "Team Musician")
    add_student(contest_roster, "No Team Musician")
    join_roster_team(contest_roster, first, "Current Team")
    join_roster_team(contest_roster, first, "Old Team")
    response = signed_client().get("/band-director/dashboard")
    rows = response.context["students"]
    team_row = next(row for row in rows if row["display_name"] == "Team Musician")
    assert team_row["team"] == {"name": "Current Team", "emblem": {
        "key": "emoji:goat", "kind": "emoji", "value": "🐐",
    }}
    assert "Old Team" not in response.text
    assert "team-emblem-emoji" in response.text and "🐐" in response.text
    assert "No team" in response.text
    assert "Overall rank" not in response.text


def test_global_positions_and_participation_expose_only_authorized_student(contest_roster):
    from sqlalchemy import select
    allowed = add_student(contest_roster, "Allowed Player")
    outsider = add_student(contest_roster, "Secret Rival", verifier_id=2)
    parent = add_student(contest_roster, "Private Parent Student", role="parent")
    for student, minutes in [(allowed, 20), (outsider, 90), (parent, 50)]:
        add_chart(contest_roster, student, date(2026, 9, 9), minutes, status="approved")
    add_chart(contest_roster, allowed, date(2026, 9, 9), 10, status="pending")
    add_chart(contest_roster, allowed, date(2026, 9, 9), 500, include_contests=False)
    add_chart(contest_roster, allowed, date(2026, 9, 6), 700)
    with contest_roster() as session:
        for review in session.scalars(select(PracticeChartVerification)).all():
            review.responded_at = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
        session.commit()
    response = signed_client().get(f"/band-director/dashboard?profile_id={outsider}&verifier_id=2")
    with contest_roster() as session:
        positions = band_director_practice_students(session, verifier_id=1)[0]["contest"]["positions"]
    assert [(p["division"], p["score"], p["rank"]) for p in positions] == [
        ("open", 30, 3), ("verified", 20, 3),
    ]
    assert "Secret Rival" not in response.text
    assert "Private Parent Student" not in response.text
    assert "Secret Rival" not in repr(response.context["students"])
    assert "Overall rank" not in response.text
    # A later approval cannot enter Verified after the existing deadline.
    with contest_roster() as session:
        review = session.scalar(select(PracticeChartVerification).join(PracticeChart).where(
            PracticeChart.profile_id == allowed, PracticeChart.minutes == 20))
        review.responded_at = datetime(2026, 9, 15, tzinfo=timezone.utc)
        session.commit()
    with contest_roster() as session:
        positions = band_director_practice_students(session, verifier_id=1)[0]["contest"]["positions"]
    assert [p["division"] for p in positions] == ["open"]


def test_finalized_positions_and_emblem_use_persisted_history(contest_roster):
    from sqlalchemy import select
    student = add_student(contest_roster, "Historical Musician")
    join_roster_team(contest_roster, student, "Current Team")
    add_chart(contest_roster, student, date(2026, 9, 9), 999)
    with contest_roster() as session:
        week = session.scalar(select(ContestWeek))
        week.status = "finalized"
        team = session.scalar(select(Team).where(Team.display_name == "Snapshot Team"))
        session.add(TeamWeekMembershipSnapshot(contest_week_id=week.id, profile_id=student,
                    team_id=team.id, snapshot_at=datetime(2026, 9, 14, tzinfo=timezone.utc)))
        contest = Contest(key="weekly-points-leaders", name="Practice Minutes this Week",
                          subject_type="student", metric_type="practice_minutes")
        session.add(contest)
        session.flush()
        session.add(ContestResult(contest_week_id=week.id, contest_id=contest.id,
                    division="verified", subject_type="student", subject_key=str(student),
                    profile_id=student, display_name_snapshot="Old private display",
                    score=25, rank=2, medal="silver"))
        session.commit()
    response = signed_client().get("/band-director/dashboard")
    with contest_roster() as session:
        row = band_director_practice_students(session, verifier_id=1)[0]
    assert response.context["students"][0]["team"]["name"] == "Current Team"
    assert row["contest"]["emblem"] == {"key": "shield:blue", "kind": "shield", "value": "Blue"}
    assert row["contest"]["positions"] == [{"label": "Practice", "division": "verified",
                                              "rank": 2, "score": 25, "unit": "minutes"}]
    assert "team-emblem-emoji" in response.text
    assert "Old private display" not in response.text


@pytest.mark.parametrize("finalized,snapshot", [(False, False), (True, False), (False, True)])
def test_local_week_emblem_respects_live_membership_and_frozen_no_team(contest_roster, finalized, snapshot):
    from sqlalchemy import select
    from app.band_director_context import _contest_week_emblem

    student = add_student(contest_roster, "Emblem Musician")
    join_roster_team(contest_roster, student, "Current Team")
    with contest_roster() as session:
        week = session.scalar(select(ContestWeek))
        if finalized:
            week.status = "finalized"
        if snapshot:
            session.add(TeamWeekMembershipSnapshot(
                contest_week_id=week.id, profile_id=student, team_id=None,
                snapshot_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
            ))
        session.flush()
        emblem = _contest_week_emblem(session, profile_id=student, week=week)
        if finalized or snapshot:
            assert emblem is None
        else:
            assert emblem == {"key": "emoji:goat", "kind": "emoji", "value": "🐐"}


def test_missing_season_is_read_only_and_has_no_data_state(roster_db):
    from sqlalchemy import func, select
    add_student(roster_db, "New Program")
    response = signed_client().get("/band-director/dashboard")
    assert "No team" in response.text
    assert "Overall rank" not in response.text
    with roster_db() as session:
        assert session.scalar(select(func.count()).select_from(Season)) == 0
        assert session.scalar(select(func.count()).select_from(ContestWeek)) == 0


def test_activity_only_participation_uses_existing_open_points(contest_roster):
    from app.models import CampPointAward
    student = add_student(contest_roster, "Activity Musician")
    with contest_roster() as session:
        session.add(CampPointAward(profile_id=student, activity_type="care", points_awarded=1,
                    occurred_at=datetime(2026, 9, 9, 18, tzinfo=timezone.utc),
                    duplicate_key="director-test-care"))
        session.commit()
    with contest_roster() as session:
        row = band_director_practice_students(session, verifier_id=1)[0]
    assert row["contest"]["positions"] == [{"label": "Board activity", "division": "open",
                                              "rank": 1, "score": 1, "unit": "points"}]


def assigned_review(factory, *, reviewer=1, role="band_director", status="pending"):
    from sqlalchemy import select
    student = add_student(factory, "Review Musician", role=role)
    add_chart(factory, student, date(2026, 9, 9), 20, status=status, reviewer=reviewer)
    with factory() as session:
        return session.scalar(select(PracticeChartVerification.id)), student


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_director_cannot_review_historical_assigned_chart(roster_db, decision):
    review_id, _ = assigned_review(roster_db)
    client = signed_client()
    assert client.get("/trusted-verifiers/practice-charts").json()["pending_charts"] == []
    assert client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                       json={"decision": decision}).status_code == 400
    with roster_db() as session:
        assert session.get(PracticeChartVerification, review_id).status == "pending"


def test_roster_membership_does_not_allow_review_of_another_verifiers_assignment(roster_db):
    review_id, _ = assigned_review(roster_db, reviewer=2)
    client = signed_client()
    page = client.get("/band-director/dashboard")
    assert "Review Musician" in page.text
    assert "data-band-director-review" not in page.text
    assert client.get("/trusted-verifiers/practice-charts").json()["pending_charts"] == []
    response = client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                           json={"decision": "approved"})
    assert response.status_code == 404
    with roster_db() as session:
        assert session.get(PracticeChartVerification, review_id).status == "pending"


def test_other_verifier_and_unauthenticated_requests_cannot_review(roster_db):
    review_id, _ = assigned_review(roster_db)
    endpoint = f"/trusted-verifiers/practice-charts/{review_id}/respond"
    assert signed_client("other@example.com").post(endpoint, json={"decision": "rejected"}).status_code == 404
    assert TestClient(main.app).post(endpoint, json={"decision": "approved"}).status_code == 401


def test_disconnect_after_page_load_revokes_review_authority(roster_db):
    from sqlalchemy import select
    review_id, student_id = assigned_review(roster_db, role="verifier")
    client = signed_client()
    assert client.get("/trusted-verifiers/practice-charts").json()["pending_charts"][0]["verification_id"] == review_id
    with roster_db() as session:
        connection = session.scalar(select(StudentVerifierConnection).where(
            StudentVerifierConnection.profile_id == student_id))
        connection.status = "disconnected"
        session.commit()
    assert client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                       json={"decision": "approved"}).status_code == 400
    assert "Review Musician" not in client.get("/band-director/dashboard").text


@pytest.mark.parametrize("role", ["verifier"])
def test_non_director_review_stays_in_original_workflow(roster_db, role):
    review_id, _ = assigned_review(roster_db, role=role)
    client = signed_client()
    html = client.get("/band-director/dashboard").text
    assert "Review Musician" not in html and "data-band-director-review" not in html
    # Existing trusted-verifier review permissions do not depend on director role.
    queue = client.get("/trusted-verifiers/practice-charts").json()
    assert queue["pending_charts"][0]["verification_id"] == review_id
    assert client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                       json={"decision": "approved"}).status_code == 200
    assert client.get("/trusted-verifiers/practice-charts").json()["pending_charts"] == []


def test_open_and_pristine_charts_have_no_review_actions(roster_db):
    student = add_student(roster_db, "Self Report")
    add_chart(roster_db, student, date(2026, 9, 9), 20)
    with roster_db() as session:
        session.add(PracticeChart(profile_id=student, practice_date=date(2026, 9, 9),
                    minutes=5, instrument="Trumpet", source="pristine", detected_playing_seconds=300))
        session.commit()
    html = signed_client().get("/band-director/dashboard").text
    assert "Self Report" in html and "Pristine Min Wk" in html
    assert signed_client().get("/trusted-verifiers/practice-charts").json()["pending_charts"] == []
    assert "data-band-director-review" not in html
