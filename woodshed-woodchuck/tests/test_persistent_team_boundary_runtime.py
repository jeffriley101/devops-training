"""Staged boundary behavior through working HTTP and real competition readers.

Every database is disposable. PostgreSQL cases exercise the actual authority
fence and the real stage/activate transaction, rather than flipping ORM flags.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from app import contests, models as m, practice_chart_routes, practice_charts, team_authority, teams
from app import persistent_team_cutover as cutover
from app.age_privacy import declare_age
from app.band_director_context import _contest_week_emblem
from tests.test_arcade_economy import signed_client
from tests.test_persistent_team_cutover import seed, TEAM_IDS, MEMBERSHIP_IDS
from tests.test_team_families import disposable_url


BEFORE = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
BOUNDARY = datetime(2026, 10, 5, 5, tzinfo=timezone.utc)


@pytest.fixture(params=["sqlite", "postgresql"])
def boundary_db(request, tmp_path, monkeypatch):
    url = disposable_url(tmp_path, request.param)
    engine = seed(url)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    clock = {"at": BEFORE}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["at"].astimezone(tz) if tz else clock["at"].replace(tzinfo=None)

    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    for module in (contests, practice_chart_routes, practice_charts, team_authority, teams, m):
        monkeypatch.setattr(module, "datetime", Clock)
    client, profile = signed_client(factory, "BOUNDARY")
    with factory() as session:
        # Exactly twenty approved current members, including the HTTP student.
        # The old Band Camp rows remain unended, unapproved historical evidence.
        first = session.get(m.TeamMembership, MEMBERSHIP_IDS[0])
        first.profile_id = profile.id
        for membership in session.scalars(select(m.TeamMembership).where(
                m.TeamMembership.id.in_(MEMBERSHIP_IDS))):
            membership.team_id = 10
        for person in session.scalars(select(m.WoodchuckProfile)):
            declare_age(session, person.id, "adult",
                        at=datetime(2000, 1, 1, tzinfo=timezone.utc))
        contests.ensure_contest_definitions(session)
        session.commit()
    db = SimpleNamespace(url=url, engine=engine, factory=factory,
                         client=client, profile=profile, clock=clock)
    yield db
    client.close()
    engine.dispose()


def stage(db):
    plan = cutover.generate_plan(db.url, TEAM_IDS, MEMBERSHIP_IDS, BOUNDARY.date(),
                                 now=db.clock["at"])
    cutover.apply_cutover(db.url, plan, plan["plan_sha256"],
        acknowledgments={ack: True for ack in cutover.ACKS},
        confirmation=cutover.CONFIRMATION, now=db.clock["at"])
    return plan


def activate(db, plan):
    return cutover.activate_cutover(db.url, plan, plan["plan_sha256"],
        acknowledgments={ack: True for ack in cutover.ACKS},
        confirmation=cutover.ACTIVATION_CONFIRMATION, now=db.clock["at"])


def book(db, day, key, minutes=30):
    return db.client.post("/practice-charts", json={"practice_date": day.isoformat(),
        "minutes": minutes, "submission_key": key, "include_team_contests": True})


def board(db, activity):
    day = db.clock["at"].astimezone(contests.CENTRAL).date()
    if activity == "trivia":
        question = contests.trivia_question_for(day)
        return db.client.post("/contests/trivia/answer", json={
            "activity_date": day.isoformat(), "selected_answer_id": question["correct_answer_id"]})
    return db.client.post("/contests/camp-points/awards", json={
        "activity_date": day.isoformat(), "activity_type": activity})


def approve_book(db, key):
    with db.factory() as session:
        chart = session.scalar(select(m.PracticeChart).where(m.PracticeChart.submission_key == key))
        session.add(m.PracticeChartVerification(practice_chart_id=chart.id,
            status="approved", responded_at=db.clock["at"] + timedelta(seconds=1)))
        session.commit()
        return chart.id


def legacy_closing_roster(db):
    """A nonempty legacy closing week, independent of the supplied Halloween case."""
    with db.factory() as session:
        session.get(m.Season, 2).ends_on = date(2026, 10, 4)
        session.get(m.Season, 3).starts_on = date(2026, 10, 5)
        session.get(m.ContestWeek, 2).season_id = 2
        session.commit()


def test_staging_keeps_http_earning_fully_legacy_and_due_boundary_fails_closed(boundary_db):
    db = boundary_db
    plan = stage(db)
    result = book(db, date(2026, 10, 4), "before-boundary")
    assert result.status_code == 201, result.text
    assert board(db, "care").status_code == 200
    approve_book(db, "before-boundary")
    with db.factory() as session:
        assert not team_authority.persistent_enabled(session, BEFORE)
        assert not session.scalar(select(func.count()).select_from(m.Team).where(m.Team.is_operating))
        assert not session.scalar(select(func.count()).select_from(m.TeamMembership).where(m.TeamMembership.is_persistent))
        chart = session.scalar(select(m.PracticeChart).where(m.PracticeChart.submission_key == "before-boundary"))
        award = session.scalar(select(m.CampPointAward).where(m.CampPointAward.duplicate_key.like("%2026-10-04:care")))
        assert chart.team_id is None and award.team_id is None
        week = session.get(m.ContestWeek, 2)
        assert week.team_membership_rules_version == team_authority.LEGACY_RULES
        assert contests._eligible_weekly_team_rosters(session, week) == {}
        assert contests._weekly_team_scores(session, week)["open"]["totals"] == {}
        assert contests._weekly_team_activity_point_scores(session, week) == {}
        charts_before = session.scalar(select(func.count()).select_from(m.PracticeChart))
        awards_before = session.scalar(select(func.count()).select_from(m.CampPointAward))
    db.clock["at"] = BOUNDARY
    assert book(db, date(2026, 10, 5), "blocked-boundary").status_code == 503
    assert board(db, "hours").status_code == 503
    assert db.client.get("/teams").status_code == 503
    with db.factory() as session:
        # A caller-provided historical instant cannot bypass the wall-clock gate.
        with pytest.raises(HTTPException) as error:
            team_authority.persistent_enabled(session, BEFORE)
        assert error.value.status_code == 503
        assert session.scalar(select(func.count()).select_from(m.PracticeChart)) == charts_before
        assert session.scalar(select(func.count()).select_from(m.CampPointAward)) == awards_before
    activate(db, plan)  # Ordinary pre-boundary activity must not invalidate approval.
    retry = book(db, date(2026, 10, 4), "before-boundary")
    assert retry.status_code == 201, retry.text
    late = book(db, date(2026, 10, 4), "late-without-legacy-roster", minutes=10)
    assert late.status_code == 201, late.text
    with db.factory() as session:
        assert team_authority.persistent_enabled(session, BOUNDARY)
        assert session.get(m.ContestWeek, 3).team_membership_rules_version == team_authority.PERSISTENT_RULES
        assert session.get(m.ContestWeek, 2).team_membership_rules_version == team_authority.LEGACY_RULES
        # Existing NULL evidence, including the new pre-boundary chart, is untouched.
        assert session.scalar(select(m.PracticeChart).where(
            m.PracticeChart.submission_key == "before-boundary")).team_id is None
        assert session.scalar(select(m.PracticeChart).where(
            m.PracticeChart.submission_key == "late-without-legacy-roster")).team_id is None
        assert session.scalar(select(func.count()).select_from(m.PracticeChart).where(
            m.PracticeChart.submission_key == "before-boundary")) == 1
        closing = session.get(m.ContestWeek, 2)
        assert team_authority.utc(closing.team_roster_frozen_at) == BOUNDARY
        assert contests._snapshot_memberships(session, closing) == []
        assert _contest_week_emblem(session, profile_id=db.profile.id, week=closing) is None


def test_pre_boundary_http_switch_requires_new_approval_and_never_promotes_guessed_membership(boundary_db):
    db = boundary_db
    legacy_closing_roster(db)
    plan = stage(db)
    switched = db.client.post("/teams/selection", json={"team_id": 11})
    assert switched.status_code == 200, switched.text
    db.clock["at"] = BOUNDARY
    with pytest.raises(cutover.CutoverError):
        activate(db, plan)
    with db.factory() as session:
        assert session.get(m.PersistentTeamControl, 1).activated_at is None
        assert not session.scalar(select(func.count()).select_from(m.Team).where(m.Team.is_operating))
        assert not session.scalar(select(func.count()).select_from(m.TeamMembership).where(m.TeamMembership.is_persistent))
        assert session.get(m.ContestWeek, 2).team_roster_frozen_at is None
        assert session.get(m.ContestWeek, 3).team_membership_rules_version == team_authority.LEGACY_RULES
        replacement = session.scalar(select(m.TeamMembership).where(
            m.TeamMembership.profile_id == db.profile.id, m.TeamMembership.ended_at.is_(None)))
        assert replacement.team_id == 11 and replacement.id not in MEMBERSHIP_IDS
        assert not replacement.is_persistent
    assert book(db, date(2026, 10, 5), "refused-stale-boundary").status_code == 503


def test_activated_week_uses_one_authority_for_points_tpr_emblems_and_rewards(boundary_db):
    db = boundary_db
    plan = stage(db)
    db.clock["at"] = BOUNDARY
    activate(db, plan)
    db.clock["at"] = BOUNDARY + timedelta(hours=13)
    result = book(db, date(2026, 10, 5), "after-boundary")
    assert result.status_code == 201, result.text
    approve_book(db, "after-boundary")
    for activity in ("care", "hours", "marching", "trivia"):
        result = board(db, activity)
        assert result.status_code == 200, result.text
    with db.factory() as session:
        week = session.get(m.ContestWeek, 3)
        members = set(session.scalars(select(m.TeamMembership.profile_id).where(
            m.TeamMembership.id.in_(MEMBERSHIP_IDS))))
        assert len(members) == 20
        assert contests._eligible_weekly_team_rosters(session, week) == {10: members}
        scores = contests._weekly_team_scores(session, week)
        assert scores["open"]["totals"] == {10: 30}
        assert scores["open"]["tpr"] == {10: 9.2}
        assert scores["verified"]["totals"] == {10: 30}
        assert contests._weekly_team_activity_point_scores(session, week) == {10: 4}
        assert contests._student_emblem_keys_for_week(session,
            contest_week=week, profile_ids={db.profile.id}) == {db.profile.id: "letter:A"}
        display = contests.team_leaderboards(session, season=session.get(m.Season, 3), contest_week=week)
        assert display["team-weekly-practice"]["open"][0]["team_id"] == 10
        assert display["team-practice-rating"]["open"][0]["score"] == 9.2
        assert display["team-lifetime-practice"]["open"][0]["team_id"] == 10
        final_at = datetime(2026, 10, 12, 18, tzinfo=timezone.utc)
        contests.finalize_contest_week(session, week_start=week.week_start, now=final_at)
        session.commit()
        assert week.finalizer_rules_version == contests.PERSISTENT_FINALIZER_RULES_VERSION
        snapshots = session.scalars(select(m.TeamWeekMembershipSnapshot).where(
            m.TeamWeekMembershipSnapshot.contest_week_id == week.id)).all()
        assert len(snapshots) == 20
        assert {row.team_id for row in snapshots} == {10}
        team_results = session.scalars(select(m.ContestResult).where(
            m.ContestResult.contest_week_id == week.id, m.ContestResult.subject_type == "team")).all()
        assert team_results and {row.team_id for row in team_results} == {10}
        grants = session.scalars(select(m.RewardGrant).where(
            m.RewardGrant.contest_result_id.in_([row.id for row in team_results]),
            m.RewardGrant.reward_type == "dandelion")).all()
        assert {row.profile_id for row in grants} == {db.profile.id}
        assert {row.contest_result_id for row in grants} == {row.id for row in team_results}
        awards = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.activity_type == "contest-placement",
            m.CampPointAward.duplicate_key.like(f"contest:{week.id}:%"))).all()
        assert awards and {row.team_id for row in awards} == {10}


def test_exact_monday_switch_preserves_closing_roster_and_late_sunday_book(boundary_db):
    db = boundary_db
    legacy_closing_roster(db)
    plan = stage(db)
    db.clock["at"] = BOUNDARY
    activate(db, plan)
    result = db.client.post("/teams/selection", json={"team_id": 11})
    assert result.status_code == 200, result.text
    with db.factory() as session:
        old = session.get(m.TeamMembership, MEMBERSHIP_IDS[0])
        assert team_authority.utc(old.ended_at) == BOUNDARY
        assert team_authority.effective_membership(session, db.profile.id,
            BOUNDARY - timedelta(microseconds=1)).team_id == 10
        assert team_authority.effective_membership(session, db.profile.id, BOUNDARY).team_id == 11
        closing = session.get(m.ContestWeek, 2)
        assert team_authority.utc(closing.team_roster_frozen_at) == BOUNDARY
        frozen = contests._snapshot_memberships(session, closing)
        assert len(frozen) == 20
        assert next(row for row in frozen if row.profile_id == db.profile.id).team_id == 10
        assert contests._student_emblem_keys_for_week(session,
            contest_week=closing, profile_ids={db.profile.id}) == {db.profile.id: "letter:A"}
        assert _contest_week_emblem(session, profile_id=db.profile.id, week=closing)["key"] == "letter:A"
        before = [dict(row) for row in session.execute(select(m.TeamWeekMembershipSnapshot.__table__).where(
            m.TeamWeekMembershipSnapshot.contest_week_id == closing.id)).mappings()]
    result = book(db, date(2026, 10, 4), "late-sunday")
    assert result.status_code == 201, result.text
    result = book(db, date(2026, 10, 5), "new-monday", minutes=10)
    assert result.status_code == 201, result.text
    approve_book(db, "late-sunday")
    with db.factory() as session:
        charts = {row.submission_key: row.team_id for row in session.scalars(select(m.PracticeChart).where(
            m.PracticeChart.submission_key.in_(("late-sunday", "new-monday"))))}
        assert charts == {"late-sunday": 10, "new-monday": 11}
        closing = session.get(m.ContestWeek, 2)
        scores = contests._weekly_team_scores(session, closing)
        assert scores["open"]["totals"] == {10: 30}
        assert scores["open"]["tpr"] == {10: 9.2}
        contests.finalize_contest_week(session, week_start=closing.week_start,
            now=datetime(2026, 10, 5, 18, tzinfo=timezone.utc))
        session.commit()
        after = [dict(row) for row in session.execute(select(m.TeamWeekMembershipSnapshot.__table__).where(
            m.TeamWeekMembershipSnapshot.contest_week_id == closing.id)).mappings()]
        assert after == before
        assert closing.finalizer_rules_version == contests.FINALIZER_RULES_VERSION
        assert _contest_week_emblem(session, profile_id=db.profile.id, week=closing)["key"] == "letter:A"
        rewards = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.activity_type == "contest-placement",
            m.CampPointAward.duplicate_key.like(f"contest:{closing.id}:%"))).all()
        assert rewards and {row.team_id for row in rewards} == {10}


def test_late_book_respects_original_finalized_snapshots_without_new_freeze_marker(boundary_db):
    db = boundary_db
    legacy_closing_roster(db)
    # Backward-compatible reader input: a finalized legacy week already has
    # authoritative snapshots, but predates p21's explicit empty-roster marker.
    with db.factory() as session:
        control = session.get(m.PersistentTeamControl, 1)
        control.activated_at, control.rules_from_week_start = BOUNDARY, BOUNDARY.date()
        session.get(m.ContestWeek, 3).team_membership_rules_version = team_authority.PERSISTENT_RULES
        for team in session.scalars(select(m.Team).where(m.Team.id.in_(TEAM_IDS))):
            team.is_operating = True
        for member in session.scalars(select(m.TeamMembership).where(m.TeamMembership.id.in_(MEMBERSHIP_IDS))):
            member.is_persistent = True
        closing = session.get(m.ContestWeek, 2)
        closing.status, closing.finalized_at = "finalized", BOUNDARY + timedelta(hours=13)
        snapshot = m.TeamWeekMembershipSnapshot(contest_week_id=closing.id,
            profile_id=db.profile.id, team_id=10, membership_id=MEMBERSHIP_IDS[0], snapshot_at=BOUNDARY)
        result = m.ContestResult(contest_week_id=closing.id, contest_id=1, division="open",
            subject_type="team", subject_key="10", team_id=10,
            display_name_snapshot="Original Eureka", score=30, rank=1, medal="gold")
        session.add_all((snapshot, result))
        session.commit()
        historical = {
            model.__tablename__: list(session.execute(select(model.__table__)).mappings())
            for model in (m.ContestWeek, m.TeamWeekMembershipSnapshot, m.ContestResult)
        }
    db.clock["at"] = BOUNDARY + timedelta(hours=14)
    assert db.client.post("/teams/selection", json={"team_id": 11}).status_code == 200
    response = book(db, date(2026, 10, 4), "after-legacy-finalization")
    assert response.status_code == 201, response.text
    with db.factory() as session:
        chart = session.scalar(select(m.PracticeChart).where(
            m.PracticeChart.submission_key == "after-legacy-finalization"))
        assert chart.team_id == 10
        assert team_authority.effective_membership(session, db.profile.id, db.clock["at"]).team_id == 11
        assert session.get(m.ContestWeek, 2).team_roster_frozen_at is None
        for model in (m.ContestWeek, m.TeamWeekMembershipSnapshot, m.ContestResult):
            assert list(session.execute(select(model.__table__)).mappings()) == historical[model.__tablename__]


def test_activation_serializes_real_postgresql_writer_and_commits_one_visible_state(boundary_db):
    db = boundary_db
    if db.engine.dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL transaction and row-lock visibility required")
    plan = stage(db)
    db.clock["at"] = BOUNDARY
    promotion_started, release, writer_entered = Event(), Event(), Event()

    def hold_promotion(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith("UPDATE teams SET is_operating"):
            promotion_started.set()
            assert release.wait(10), "Test did not release activation transaction"

    def observe_waiting_writer(connection, cursor, statement, parameters, context, many):
        if (promotion_started.is_set() and "FOR UPDATE" in statement
                and statement.lstrip().startswith("SELECT persistent_team_control.")):
            writer_entered.set()

    # ACTIVATE owns a separate engine. Scope this SQLAlchemy connection hook by
    # exact statement and install it only for this disposable-database test.
    from sqlalchemy.engine import Engine
    event.listen(Engine, "after_cursor_execute", hold_promotion)
    event.listen(Engine, "before_cursor_execute", observe_waiting_writer)
    try:
        def writer():
            with db.factory() as session:
                team_authority.lock_authority(session)
                profile = session.get(m.WoodchuckProfile, db.profile.id)
                award, _ = contests.create_camp_point_award(session, profile=profile,
                    activity_type="care", activity_date=BOUNDARY.date(), now=BOUNDARY)
                session.commit()
                return award.team_id

        with ThreadPoolExecutor(max_workers=2) as pool:
            applying = pool.submit(activate, db, plan)
            assert promotion_started.wait(10), "ACTIVATE did not reach promotion"
            waiting = pool.submit(writer)
            assert writer_entered.wait(5)
            with db.factory() as observing:
                control = observing.get(m.PersistentTeamControl, 1)
                assert control.activated_at is None
                assert observing.get(m.ContestWeek, 3).team_membership_rules_version == team_authority.LEGACY_RULES
                assert not observing.get(m.Team, 10).is_operating
                assert not observing.get(m.TeamMembership, MEMBERSHIP_IDS[0]).is_persistent
            assert not waiting.done(), "Writer bypassed activation's singleton lock"
            release.set()
            assert applying.result(timeout=20)["transaction_state"] == "committed"
            assert waiting.result(timeout=20) == 10
    finally:
        release.set()
        event.remove(Engine, "after_cursor_execute", hold_promotion)
        event.remove(Engine, "before_cursor_execute", observe_waiting_writer)
    with db.factory() as session:
        assert team_authority.persistent_enabled(session, BOUNDARY)
        assert session.get(m.ContestWeek, 3).team_membership_rules_version == team_authority.PERSISTENT_RULES
        assert session.get(m.Team, 10).is_operating
        assert session.get(m.TeamMembership, MEMBERSHIP_IDS[0]).is_persistent
