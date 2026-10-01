"""Admission-time attribution and independent practice writers at activation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event, get_ident
from time import monotonic, sleep

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Engine

from app import contests, models as m, practice_charts, team_authority, teams
from app import persistent_team_cutover as cutover
from app.main import app
from app.security import hash_pin
from tests.test_persistent_team_boundary_runtime import (
    BOUNDARY, activate, board, book, boundary_db, legacy_closing_roster, stage,
)


SUNDAY = date(2026, 10, 4)
MONDAY = date(2026, 10, 5)
LAST_SUNDAY_INSTANT = BOUNDARY - timedelta(microseconds=1)


def chart_by_key(db, key):
    with db.factory() as session:
        return session.scalar(select(m.PracticeChart).where(
            m.PracticeChart.submission_key == key))


@pytest.mark.parametrize("practice_day", [SUNDAY, MONDAY])
def test_book_crossing_boundary_uses_one_admission_time(
        boundary_db, monkeypatch, practice_day):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = LAST_SUNDAY_INSTANT
    approved = stage(db)
    admitted = Event()
    original = team_authority.authority_write_time

    def cross_after_admission(*args, **kwargs):
        at = original(*args, **kwargs)
        if not admitted.is_set():
            admitted.set()
            db.clock["at"] = BOUNDARY
        return at

    # Patch the shared clock API, including any module-level imported aliases.
    for module in (team_authority, practice_charts):
        if getattr(module, "authority_write_time", None) is original:
            monkeypatch.setattr(module, "authority_write_time", cross_after_admission)
    response = book(db, practice_day, "crossing-admission")
    assert admitted.is_set(), "The production chart service did not admit the write"
    if practice_day == MONDAY:
        assert response.status_code == 400, response.text
        assert chart_by_key(db, "crossing-admission") is None
    else:
        assert response.status_code == 201, response.text
        chart = chart_by_key(db, "crossing-admission")
        assert chart.team_id == 10
        assert team_authority.utc(chart.created_at) == LAST_SUNDAY_INSTANT
    # A late commit of an admitted Sunday operation must not strand activation.
    assert activate(db, approved)["transaction_state"] == "committed"


def test_book_insert_before_boundary_can_commit_after_with_sunday_evidence(boundary_db):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = LAST_SUNDAY_INSTANT
    approved = stage(db)
    inserted = Event()

    def cross_after_insert(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith("INSERT INTO practice_charts"):
            inserted.set()
            db.clock["at"] = BOUNDARY

    event.listen(db.engine, "after_cursor_execute", cross_after_insert)
    try:
        response = book(db, SUNDAY, "crossing-commit")
    finally:
        event.remove(db.engine, "after_cursor_execute", cross_after_insert)
    assert inserted.is_set()
    assert response.status_code == 201, response.text
    chart = chart_by_key(db, "crossing-commit")
    assert chart.team_id == 10
    assert team_authority.utc(chart.created_at) == LAST_SUNDAY_INSTANT
    assert activate(db, approved)["transaction_state"] == "committed"


def submit_boundary_board_activity(db, activity):
    if activity == "bonus":
        current = db.client.get("/contests/bonus-challenge/current")
        assert current.status_code == 200, current.text
        challenge = current.json()["challenge"]
        return db.client.post("/contests/bonus-challenge/progress", json={
            "activity_date": challenge["activity_date"],
            "challenge_instance": challenge["instance_key"],
        })
    if activity == "incorrect_trivia":
        day = db.clock["at"].astimezone(contests.CENTRAL).date()
        question = contests.trivia_question_for(day)
        wrong = next(choice["id"] for choice in question["choices"]
                     if choice["id"] != question["correct_answer_id"])
        return db.client.post("/contests/trivia/answer", json={
            "activity_date": day.isoformat(), "selected_answer_id": wrong,
        })
    return board(db, activity)


@pytest.mark.parametrize("activity", ["care", "trivia", "incorrect_trivia", "bonus"])
def test_board_crossing_boundary_returns_success_with_sunday_attribution(
        boundary_db, monkeypatch, activity):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = LAST_SUNDAY_INSTANT
    approved = stage(db)
    admitted = Event()
    original = team_authority.authority_write_time

    def cross_after_admission(*args, **kwargs):
        at = original(*args, **kwargs)
        if not admitted.is_set():
            admitted.set()
            db.clock["at"] = BOUNDARY
        return at

    for module in (team_authority, contests):
        if getattr(module, "authority_write_time", None) is original:
            monkeypatch.setattr(module, "authority_write_time", cross_after_admission)
    response = submit_boundary_board_activity(db, activity)
    assert admitted.is_set()
    assert response.status_code == 200, response.text
    award_activity = {"bonus": "quest", "incorrect_trivia": "trivia"}.get(activity, activity)
    with db.factory() as session:
        awards = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.profile_id == db.profile.id,
            m.CampPointAward.activity_type == award_activity)).all()
        if activity == "incorrect_trivia":
            assert response.json()["correct"] is False
            assert awards == []
            attempt = session.scalars(select(m.DailyTriviaAttempt).where(
                m.DailyTriviaAttempt.profile_id == db.profile.id)).one()
            assert attempt.activity_date == SUNDAY and not attempt.correct
            assert team_authority.utc(attempt.created_at) == LAST_SUNDAY_INSTANT
        else:
            assert len(awards) == 1
            assert awards[0].team_id == 10
            assert team_authority.utc(awards[0].occurred_at) == LAST_SUNDAY_INSTANT
            assert team_authority.utc(awards[0].created_at) == LAST_SUNDAY_INSTANT
    assert activate(db, approved)["transaction_state"] == "committed"
    response = submit_boundary_board_activity(
        db, "trivia" if activity == "incorrect_trivia" else activity)
    assert response.status_code == 200, response.text
    with db.factory() as session:
        monday = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.profile_id == db.profile.id,
            m.CampPointAward.activity_type == award_activity,
            m.CampPointAward.occurred_at >= BOUNDARY)).one()
        assert monday.team_id == 10
        assert team_authority.utc(monday.occurred_at) == BOUNDARY


def test_nonhttp_session_cannot_reuse_admission_after_commit(boundary_db):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = LAST_SUNDAY_INSTANT
    approved = stage(db)
    with db.factory() as session:
        profile = session.get(m.WoodchuckProfile, db.profile.id)
        first = practice_charts.create_practice_chart_verification_request(
            session, profile=profile, verifier_id=None, practice_date=SUNDAY,
            minutes=15, submission_key="first-service-operation")
        assert first.chart.team_id == 10
        db.clock["at"] = BOUNDARY
        with pytest.raises(HTTPException) as error:
            practice_charts.create_practice_chart_verification_request(
                session, profile=profile, verifier_id=None, practice_date=MONDAY,
                minutes=15, submission_key="second-service-operation")
        assert error.value.status_code == 503
    assert chart_by_key(db, "second-service-operation") is None
    assert activate(db, approved)["transaction_state"] == "committed"


def test_explicit_second_operation_revalidates_boundary_before_commit(boundary_db):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = LAST_SUNDAY_INSTANT
    approved = stage(db)
    with db.factory() as session:
        profile = session.get(m.WoodchuckProfile, db.profile.id)
        first, created = contests.create_camp_point_award(
            session, profile=profile, activity_type="care", activity_date=SUNDAY,
            now=LAST_SUNDAY_INSTANT)
        assert created and first.team_id == 10
        db.clock["at"] = BOUNDARY
        with pytest.raises(HTTPException) as error:
            contests.create_camp_point_award(
                session, profile=profile, activity_type="care", activity_date=MONDAY,
                now=BOUNDARY)
        assert error.value.status_code == 503
        session.commit()
    with db.factory() as session:
        awards = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.profile_id == db.profile.id)).all()
        assert len(awards) == 1
        assert team_authority.utc(awards[0].occurred_at) == LAST_SUNDAY_INSTANT
    assert activate(db, approved)["transaction_state"] == "committed"


def test_team_selection_crossing_boundary_returns_legacy_change_and_stales_approval(
        boundary_db, monkeypatch):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = LAST_SUNDAY_INSTANT
    approved = stage(db)
    admitted = Event()
    original = team_authority.authority_write_time

    def cross_after_admission(*args, **kwargs):
        at = original(*args, **kwargs)
        if not admitted.is_set():
            admitted.set()
            db.clock["at"] = BOUNDARY
        return at

    for module in (team_authority, teams):
        if getattr(module, "authority_write_time", None) is original:
            monkeypatch.setattr(module, "authority_write_time", cross_after_admission)
    response = db.client.post("/teams/selection", json={"team_id": 11})
    assert admitted.is_set()
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    with db.factory() as session:
        membership = session.scalars(select(m.TeamMembership).where(
            m.TeamMembership.profile_id == db.profile.id,
            m.TeamMembership.ended_at.is_(None))).one()
        assert membership.team_id == 11 and not membership.is_persistent
        assert team_authority.utc(membership.started_at) == LAST_SUNDAY_INSTANT
    with pytest.raises(cutover.CutoverError):
        activate(db, approved)
    with db.factory() as session:
        assert session.get(m.PersistentTeamControl, 1).activated_at is None
        assert not session.scalar(select(func.count()).select_from(m.Team).where(
            m.Team.is_operating.is_(True)))


def test_family_private_practice_remains_available_while_boundary_pending(boundary_db):
    db = boundary_db
    approved = stage(db)
    db.clock["at"] = BOUNDARY
    page = db.client.get("/family/practice")
    assert page.status_code == 200, page.text
    data = db.client.get("/family/practice/data")
    assert data.status_code == 200, data.text
    response = db.client.post("/family/practice", data={
        "csrf": page.context["csrf"], "submission_key": "pending-private",
        "practice_date": MONDAY.isoformat(), "minutes": "15",
        "note": "Private practice during activation",
    })
    assert response.status_code == 200, response.text
    assert [redirect.status_code for redirect in response.history] == [303]
    chart = chart_by_key(db, "pending-private")
    assert chart is not None and chart.team_id is None
    assert not chart.include_contests and not chart.include_team_contests
    assert team_authority.utc(chart.created_at) == BOUNDARY
    assert activate(db, approved)["transaction_state"] == "committed"
    after = chart_by_key(db, "pending-private")
    assert after.id == chart.id and after.team_id is None


def test_team_report_waits_for_pending_boundary(boundary_db):
    db = boundary_db
    approved = stage(db)
    db.clock["at"] = BOUNDARY
    response = db.client.post("/teams/10/reports", json={"category": "other"})
    assert response.status_code == 503, response.text
    with db.factory() as session:
        assert session.scalar(select(func.count()).select_from(m.TeamReport).where(
            m.TeamReport.reporter_profile_id == db.profile.id)) == 0
    assert activate(db, approved)["transaction_state"] == "committed"
    response = db.client.post("/teams/10/reports", json={"category": "other"})
    assert response.status_code == 201, response.text
    with db.factory() as session:
        report = session.scalars(select(m.TeamReport).where(
            m.TeamReport.reporter_profile_id == db.profile.id)).one()
        assert report.team_id == 10
        assert team_authority.utc(report.created_at) == BOUNDARY


def pending_review(db, *, private):
    with db.factory() as session:
        verifier = m.TrustedVerifier(email="boundary-reviewer@example.test",
            display_name="Boundary Reviewer", pin_hash=hash_pin("1357"))
        session.add(verifier)
        session.flush()
        session.add(m.StudentVerifierConnection(profile_id=db.profile.id,
            verifier_id=verifier.id, status="accepted", role="verifier"))
        chart = m.PracticeChart(profile_id=db.profile.id, practice_date=SUNDAY,
            minutes=30, instrument="Flute", practice_details=[],
            submission_key="pending-review", created_at=db.clock["at"],
            include_contests=not private, include_team_contests=not private,
            team_id=None if private else 10)
        session.add(chart)
        session.flush()
        review = m.PracticeChartVerification(practice_chart_id=chart.id,
            verifier_id=verifier.id, status="pending")
        session.add(review)
        session.commit()
        return verifier.id, review.id, chart.id


@pytest.mark.parametrize("private", [False, True])
def test_verifier_http_pending_boundary_respects_chart_participation(boundary_db, private):
    db = boundary_db
    verifier_id, review_id, chart_id = pending_review(db, private=private)
    if private:
        with db.factory() as session:
            scores_before = contests._weekly_team_scores(session, session.get(m.ContestWeek, 2))
    with TestClient(app) as client:
        response = client.post("/trusted-verifiers/login", data={
            "email": "boundary-reviewer@example.test", "pin": "1357"})
        assert response.status_code == 200, response.text
        approved = stage(db)
        db.clock["at"] = BOUNDARY
        response = client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                               json={"decision": "approved"})
        assert response.status_code == (200 if private else 503), response.text
        with db.factory() as session:
            review = session.get(m.PracticeChartVerification, review_id)
            assert review.status == ("approved" if private else "pending")
            assert session.get(m.PracticeChart, chart_id).team_id == (None if private else 10)
        assert activate(db, approved)["transaction_state"] == "committed"
        if not private:
            response = client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                                   json={"decision": "approved"})
            assert response.status_code == 200, response.text
        with db.factory() as session:
            assert session.get(m.PracticeChartVerification, review_id).status == "approved"
            assert session.scalar(select(func.count()).select_from(m.RewardGrant).where(
                m.RewardGrant.source_key == f"practice-chart:{chart_id}")) == 1
            if private:
                closing_week = session.get(m.ContestWeek, 2)
                charts, approved_ids, _ = contests._charts_and_approved_ids(session, closing_week)
                assert chart_id not in {chart.id for chart in charts}
                assert chart_id not in approved_ids
                assert contests._weekly_team_scores(session, closing_week) == scores_before


def wait_for_postgres_lock(db, pid):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        with db.factory() as session:
            waiting = session.scalar(text(
                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"),
                {"pid": pid})
        if waiting:
            return
        sleep(0.01)
    pytest.fail(f"PostgreSQL backend {pid} did not reach the expected lock wait")


@pytest.mark.parametrize("first", ["writer", "activation"])
@pytest.mark.parametrize("kind", ["private_chart", "private_verification", "public_verification"])
def test_practice_writers_and_activation_serialize_in_both_orders(
        boundary_db, monkeypatch, first, kind):
    db = boundary_db
    if db.engine.dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL row/table lock ordering required")
    legacy_closing_roster(db)  # Activation inserts profile-FK roster evidence.
    review_ids = (pending_review(db, private=kind == "private_verification")
                  if "verification" in kind else None)
    approved = stage(db)
    db.clock["at"] = LAST_SUNDAY_INSTANT if first == "writer" else BOUNDARY
    held, release, other_started = Event(), Event(), Event()
    actors, pids = {}, {}
    original_lock_state = practice_charts.lock_state

    def hold_profile(session, profile_id):
        state = original_lock_state(session, profile_id)
        if first == "writer" and actors.get(get_ident()) == "writer":
            held.set()
            assert release.wait(10), "Writer was not released"
        return state

    def observe_sql(connection, cursor, statement, parameters, context, many):
        actor = actors.get(get_ident())
        if actor:
            pids[actor] = connection.connection.driver_connection.info.backend_pid
            if held.is_set() and actor != first:
                other_started.set()

    def hold_activation(connection, cursor, statement, parameters, context, many):
        if (first == "activation" and actors.get(get_ident()) == "activation"
                and statement.lstrip().startswith("UPDATE teams SET is_operating")):
            held.set()
            assert release.wait(10), "Activation was not released"

    def writer():
        actors[get_ident()] = "writer"
        with db.factory() as session:
            if kind == "private_chart":
                created = practice_charts.create_practice_chart_verification_request(
                    session, profile=session.get(m.WoodchuckProfile, db.profile.id),
                    verifier_id=None, practice_date=SUNDAY, minutes=15,
                    submission_key="racing-private", include_contests=False,
                    include_team_contests=False)
                return created.chart.id
            verifier_id, review_id, _ = review_ids
            result = practice_charts.respond_to_practice_chart_verification(
                session, verifier=session.get(m.TrustedVerifier, verifier_id),
                verification_id=review_id, decision="approved")
            return result.id

    def activating():
        actors[get_ident()] = "activation"
        return activate(db, approved)

    monkeypatch.setattr(practice_charts, "lock_state", hold_profile)
    event.listen(Engine, "before_cursor_execute", observe_sql)
    event.listen(Engine, "after_cursor_execute", hold_activation)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(writer if first == "writer" else activating)
            assert held.wait(10), "First operation did not reach its held lock"
            db.clock["at"] = BOUNDARY
            second_future = pool.submit(activating if first == "writer" else writer)
            assert other_started.wait(5)
            second_actor = "activation" if first == "writer" else "writer"
            wait_for_postgres_lock(db, pids[second_actor])
            assert not second_future.done()
            release.set()
            first_result = first_future.result(timeout=15)
            second_result = second_future.result(timeout=15)
            activation_result = second_result if first == "writer" else first_result
            assert activation_result["transaction_state"] == "committed"
    finally:
        release.set()
        event.remove(Engine, "before_cursor_execute", observe_sql)
        event.remove(Engine, "after_cursor_execute", hold_activation)
    with db.factory() as session:
        assert session.get(m.PersistentTeamControl, 1).activated_at is not None
        assert session.scalar(select(func.count()).select_from(m.TeamWeekMembershipSnapshot).where(
            m.TeamWeekMembershipSnapshot.contest_week_id == 2)) == 20
        if review_ids:
            review = session.get(m.PracticeChartVerification, review_ids[1])
            assert review.status == "approved"
            assert session.get(m.PracticeChart, review_ids[2]).team_id == (
                None if kind == "private_verification" else 10)
            if kind == "public_verification":
                assert team_authority.utc(review.responded_at) == (
                    LAST_SUNDAY_INSTANT if first == "writer" else BOUNDARY)
        else:
            chart = session.scalar(select(m.PracticeChart).where(
                m.PracticeChart.submission_key == "racing-private"))
            assert chart.team_id is None and not chart.include_contests
