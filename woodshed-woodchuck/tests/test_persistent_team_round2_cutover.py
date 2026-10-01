"""Cutover isolation and calendar authority regressions on disposable databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import models as m, persistent_team_cutover as cutover
from tests.test_persistent_team_cutover import (
    BOUNDARY, BOUNDARY_AT, MEMBERSHIP_IDS, NOW, TEAM_IDS,
    activate, db, plan, snapshot, stage,
)


def empty_membership_plan(db):
    with Session(db[1]) as session:
        for membership_id in MEMBERSHIP_IDS:
            session.get(m.TeamMembership, membership_id).ended_at = NOW - timedelta(hours=1)
        session.commit()
    return cutover.generate_plan(db[0], TEAM_IDS, [], BOUNDARY, now=NOW)


@pytest.mark.parametrize("operation", ["apply", "activate"])
@pytest.mark.parametrize("isolation,error", [
    ("REPEATABLE READ", "persistent_team_cutover_requires_read_committed"),
    ("SERIALIZABLE", "persistent_team_cutover_requires_read_committed"),
    ("AUTOCOMMIT", "persistent_team_cutover_requires_transactional_connection"),
])
def test_cutover_rejects_unsupported_transactions_before_authority_read(
    db, monkeypatch, operation, isolation, error,
):
    if db[1].dialect.name != "postgresql":
        pytest.skip("PostgreSQL transaction isolation is required")
    approved = empty_membership_plan(db)
    if operation == "activate":
        stage(db, approved)
    before = snapshot(db[1])
    statements = []
    create_engine = cutover.create_engine

    def configured_engine(*args, **kwargs):
        engine = create_engine(*args, **dict(kwargs, isolation_level=isolation))
        event.listen(engine, "before_cursor_execute",
                     lambda connection, cursor, sql, parameters, context, many: statements.append(sql))
        return engine

    monkeypatch.setattr(cutover, "create_engine", configured_engine)
    with pytest.raises(cutover.CutoverError, match=error):
        (stage if operation == "apply" else activate)(db, approved)
    assert not any("persistent_team_control" in sql for sql in statements)
    assert snapshot(db[1]) == before


def test_read_committed_can_stage_activate_and_verify_without_memberships(db, monkeypatch):
    if db[1].dialect.name != "postgresql":
        pytest.skip("PostgreSQL transaction isolation is required")
    approved = empty_membership_plan(db)
    create_engine = cutover.create_engine
    monkeypatch.setattr(cutover, "create_engine", lambda *args, **kwargs:
                        create_engine(*args, **dict(kwargs, isolation_level="READ COMMITTED")))
    stage(db, approved)
    receipt = activate(db, approved)
    assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]
    after = snapshot(db[1])
    assert after["persistent_team_control"][0]["activated_at"] is not None
    assert not any(row["is_persistent"] for row in after["team_memberships"])


def test_existing_repeatable_read_snapshot_cannot_hide_new_legacy_membership(db, monkeypatch):
    if db[1].dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL snapshots and concurrent transactions are required")
    from app import team_authority

    approved = empty_membership_plan(db)
    stage(db, approved)

    class BeforeBoundary(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = BOUNDARY_AT - timedelta(microseconds=1)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(team_authority, "datetime", BeforeBoundary)
    repeatable = cutover.create_engine(db[0], isolation_level="REPEATABLE READ")
    try:
        with repeatable.connect() as connection:
            # Establish the old snapshot at the DBAPI level so activation owns
            # SQLAlchemy's transaction wrapper, as it does for a fresh engine.
            raw = connection.connection.driver_connection
            count_query = "SELECT count(*) FROM team_memberships WHERE team_id = 10 AND ended_at IS NULL"
            assert raw.execute(count_query).fetchone()[0] == 0
            assert not connection.in_transaction()

            def legacy_writer():
                with Session(db[1]) as session:
                    team_authority.lock_authority(session)
                    session.add(m.TeamMembership(profile_id=24, team_id=10, season_id=2,
                        started_at=BOUNDARY_AT - timedelta(seconds=1),
                        selected_week_start=BOUNDARY - timedelta(days=7)))
                    session.commit()

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(legacy_writer).result(timeout=15)
            # The committed row is invisible to the old repeatable snapshot.
            assert raw.execute(count_query).fetchone()[0] == 0
            before = snapshot(db[1])
            assert len([row for row in before["team_memberships"]
                        if row["team_id"] == 10 and row["ended_at"] is None]) == 1
            statements = []
            event.listen(connection, "before_cursor_execute",
                lambda conn, cursor, sql, parameters, context, many: statements.append(sql))
            monkeypatch.setattr(cutover, "create_engine", lambda *args, **kwargs:
                                SimpleNamespace(connect=lambda: connection, dispose=lambda: None))
            with pytest.raises(cutover.CutoverError,
                               match="persistent_team_cutover_requires_read_committed"):
                activate(db, approved)
            assert not any("persistent_team_control" in sql for sql in statements)
        assert snapshot(db[1]) == before
    finally:
        repeatable.dispose()


def test_rolled_back_savepoint_cannot_preserve_preboundary_admission(db, monkeypatch):
    if db[1].dialect.name != "postgresql":
        pytest.skip("PostgreSQL savepoint rollback releases acquired row locks")
    from app import contests, team_authority

    approved = plan(db)
    stage(db, approved)
    before = snapshot(db[1])
    clock = {"at": BOUNDARY_AT - timedelta(microseconds=1)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["at"].astimezone(tz) if tz else clock["at"].replace(tzinfo=None)

    monkeypatch.setattr(team_authority, "datetime", Clock)
    with Session(db[1]) as session:
        profile = session.get(m.WoodchuckProfile, 24)
        savepoint = session.begin_nested()
        assert team_authority.authority_write_time(session) == clock["at"]
        savepoint.rollback()
        # Another transaction can now acquire the singleton: the old admission
        # no longer owns its serialization fence even though the root survives.
        with db[1].begin() as independent:
            independent.execute(text("SELECT id FROM persistent_team_control WHERE id=1 FOR UPDATE NOWAIT"))
        clock["at"] = BOUNDARY_AT
        with pytest.raises(HTTPException) as refused:
            contests.create_camp_point_award(session, profile=profile, activity_type="care",
                activity_date=BOUNDARY - timedelta(days=1), now=BOUNDARY_AT)
        assert refused.value.status_code == 503
        session.commit()
    assert snapshot(db[1]) == before


@pytest.mark.parametrize("field,value", [
    ("status", "closed"),
    ("starts_on", date(2026, 9, 29)),
    ("ends_on", date(2026, 10, 4)),
    ("key", "changed-calendar-identity"),
    ("timezone", "UTC"),
])
def test_changed_staged_calendar_authority_requires_fresh_approval(db, field, value):
    approved = plan(db)
    stage(db, approved)
    with Session(db[1]) as session:
        setattr(session.get(m.Season, 3), field, value)
        session.commit()
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="staged_authority_changed"):
        activate(db, approved)
    assert snapshot(db[1]) == before


def test_season_display_name_change_does_not_change_approved_authority(db):
    approved = plan(db)
    stage(db, approved)
    with Session(db[1]) as session:
        session.get(m.Season, 3).name = "Updated presentation name"
        session.commit()
    receipt = activate(db, approved)
    assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]


@pytest.mark.parametrize("include_contests,include_team_contests", [
    (False, False), (True, False), (False, True),
])
def test_only_fully_private_postboundary_charts_leave_activation_possible(
    db, include_contests, include_team_contests,
):
    approved = plan(db)
    stage(db, approved)
    with Session(db[1]) as session:
        session.add(m.PracticeChart(profile_id=24, practice_date=BOUNDARY, minutes=10,
            instrument="Trumpet", practice_details=[], created_at=BOUNDARY_AT,
            include_contests=include_contests, include_team_contests=include_team_contests))
        session.commit()
    before = snapshot(db[1])
    if include_contests or include_team_contests:
        with pytest.raises(cutover.CutoverError, match="post_boundary_activity_requires_review"):
            activate(db, approved)
        assert snapshot(db[1]) == before
    else:
        receipt = activate(db, approved)
        assert snapshot(db[1])["practice_charts"] == before["practice_charts"]
        assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]


def test_activation_and_profile_first_login_do_not_deadlock_and_can_retry(db):
    if db[1].dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL profile and table locks are required")
    from app.age_privacy import declare_age
    from app.login_streaks import apply_daily_login

    with Session(db[1]) as session:
        session.get(m.ContestWeek, 2).season_id = 2
        declare_age(session, 24, "adult", at=NOW)
        session.commit()
    approved = plan(db)
    stage(db, approved)
    before = snapshot(db[1])
    profile_locked, roster_started = Event(), Event()

    def pause_login(connection, cursor, statement, parameters, context, many):
        if ("woodchuck_profiles.id" in statement and "FOR UPDATE" in statement
                and not profile_locked.is_set()):
            profile_locked.set()
            assert roster_started.wait(15), "Activation did not reach roster insertion"

    def observe_roster(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO team_week_membership_snapshots"):
            roster_started.set()

    def login():
        with Session(db[1]) as session:
            result = apply_daily_login(session, profile_id=24, now=BOUNDARY_AT)
            session.commit()
            return result

    event.listen(db[1], "after_cursor_execute", pause_login)
    event.listen(Engine, "before_cursor_execute", observe_roster)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            logging_in = pool.submit(login)
            assert profile_locked.wait(10), "Login did not acquire its profile lock"
            activating = pool.submit(activate, db, approved)
            assert logging_in.result(timeout=20)["awarded_today"]
            # Full evidence comparison deliberately requires a clean retry if
            # unrelated rewards change while activation is gathering evidence.
            with pytest.raises(cutover.CutoverError, match="unexpected_row_change"):
                activating.result(timeout=20)
    finally:
        roster_started.set()
        event.remove(db[1], "after_cursor_execute", pause_login)
        event.remove(Engine, "before_cursor_execute", observe_roster)
    after_failure = snapshot(db[1])
    for name in ("persistent_team_control", "teams", "team_memberships",
                 "contest_weeks", "team_week_membership_snapshots"):
        assert after_failure[name] == before[name]
    with Session(db[1]) as session:
        reward = session.scalar(select(m.RewardGrant).where(
            m.RewardGrant.profile_id == 24, m.RewardGrant.category_key == "login-streak"))
        assert reward is not None
    receipt = activate(db, approved)
    assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]


@pytest.mark.parametrize("service", ["deletion", "director_contest"])
def test_non_http_service_rejects_timestamp_from_before_activation(db, service):
    if service == "director_contest":
        with Session(db[1]) as session:
            for team_id in (1, 10):
                team = session.get(m.Team, team_id)
                team.visibility = "private"
                team.director_led = True
                team.join_code = f"STALE-CLOCK-{team_id}"
            session.add(m.ProfileCapability(profile_id=1, capability="band_director"))
            session.commit()
    approved = plan(db)
    stage(db, approved)
    activate(db, approved)
    before = snapshot(db[1])
    with Session(db[1]) as session:
        with pytest.raises(HTTPException) as refused:
            stale_instant = BOUNDARY_AT - timedelta(microseconds=1)
            if service == "deletion":
                from app.account_deletion import anonymize_woodchuck_account
                anonymize_woodchuck_account(session,
                    profile=session.get(m.WoodchuckProfile, 24), now=stale_instant)
            else:
                from app.director_dashboard import DirectorContestCreate, create_director_contest
                submitted = DirectorContestCreate(title="Historical Team must not become current",
                    starts_at=BOUNDARY_AT, ends_at=BOUNDARY_AT + timedelta(days=1),
                    finalizes_at=BOUNDARY_AT + timedelta(days=2), metric="total_minutes", team_ids=[1])
                create_director_contest(session, profile=session.get(m.WoodchuckProfile, 1),
                    submitted=submitted, now=stale_instant)
        assert refused.value.status_code == 409
        session.commit()
    assert snapshot(db[1]) == before


@pytest.mark.parametrize("phase", ["disabled", "staged", "activated"])
def test_seasonal_continuity_writer_is_retired_with_persistent_control(db, phase):
    from app.team_continuity import apply_team_continuity
    if phase != "disabled":
        approved = plan(db)
        stage(db, approved)
        if phase == "activated":
            activate(db, approved)
    before = snapshot(db[1])
    with Session(db[1]) as session:
        with pytest.raises(ValueError, match="Seasonal Team continuity is retired"):
            apply_team_continuity(session, source_season_id=2, destination_season_id=3,
                                  now=BOUNDARY_AT)
        session.commit()
    assert snapshot(db[1]) == before
