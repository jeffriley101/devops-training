"""Canonical calendar, explicit repair, and cross-feature date authority."""
from datetime import date, datetime, timedelta, timezone
from dataclasses import fields

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.team_factory import make_team

from app import main, season_maintenance
from app.band_director_context import current_roster_period
from app.band_director_dashboard import dashboard_metrics
from app.band_director_practice import band_director_practice_students
from app.board_seasons import BoardSeason, board_season_presentation
from app.contest_admin import admin_status
from app.contest_seasons import rollover_season, season_status_payload
from app.contests import (contest_week_schedule, current_contests_payload, ensure_current_contest_data,
                          _active_team_id_for_event, _current_team_member_ids)
from app.db import Base
from app.models import (Season, ContestWeek, Contest, ContestResult, Team, TeamMembership,
                        TeamWeekMembershipSnapshot, RewardGrant, CrownAward, CrownProgress,
                        WoodchuckProfile, CampPointAward, PracticeChart, StudentVerifierConnection)
from app.seasons import (CANONICAL_SEASONS, SeasonConfigurationError, bootstrap_canonical_seasons,
                         season_covering_date)
from app.season_maintenance import apply_calendar_plan, calendar_plan
from app.teams import selection_payload
from app.trusted_verifier_dashboard import verifier_dashboard_snapshot
from test_band_director_roster import roster_db, add_student, signed_client

NOW = datetime(2026, 9, 12, 18, tzinfo=timezone.utc)
BACK_TO_SCHOOL_NOW = datetime(2026, 9, 19, 18, tzinfo=timezone.utc)


def add_dual_role_student(factory, name):
    """Use distinct adults for director and verifier relationships."""
    profile_id = add_student(factory, name)
    with factory() as session:
        session.add(StudentVerifierConnection(profile_id=profile_id, verifier_id=2,
                                             role="verifier", status="accepted"))
        session.commit()
    return profile_id


@pytest.fixture
def database():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session, factory
    engine.dispose()


def legacy_data(session, *, week_count=7):
    season = Season(key="band-camp-2026", name="Band Camp", starts_on=date(2026, 7, 27), status="active")
    session.add(season)
    session.flush()
    weeks = []
    for n in range(week_count):
        start = season.starts_on + timedelta(weeks=n)
        end, deadline, finalizes = contest_week_schedule(start)
        week = ContestWeek(season_id=season.id, week_start=start, week_end=end, status="open",
                           verification_deadline_at=deadline, finalize_after=finalizes)
        session.add(week)
        weeks.append(week)
    session.commit()
    return season, weeks


@pytest.mark.parametrize("day,key", [
    ("2026-08-23", "band-camp-2026"), ("2026-08-24", "band-camp-2026"),
    ("2026-09-12", "band-camp-2026"), ("2026-09-13", "band-camp-2026"),
    ("2026-09-14", "back-to-school-2026"), ("2026-09-27", "back-to-school-2026"),
    ("2026-09-28", "halloween-2026"), ("2026-11-01", "halloween-2026"),
    ("2026-11-02", "holiday-2026"), ("2027-01-10", "holiday-2026"),
    ("2027-01-11", "hibernaculum-2027"), ("2027-03-07", "hibernaculum-2027"),
    ("2027-03-08", "spring-2027"), ("2027-05-09", "spring-2027"),
    ("2027-05-10", "beach-2027"), ("2027-07-04", "beach-2027"),
    ("2027-07-05", "band-camp-2027"),
])
def test_canonical_transitions(database, day, key):
    session, _ = database
    bootstrap_canonical_seasons(session)
    assert season_covering_date(session, date.fromisoformat(day)).key == key
    assert season_covering_date(session, date(2026, 7, 26)) is None


def test_bootstrap_idempotent_aligned_no_overlap_and_no_implicit_close(database):
    session, _ = database
    bootstrap_canonical_seasons(session)
    ids = list(session.scalars(select(Season.id)))
    bootstrap_canonical_seasons(session)
    assert list(session.scalars(select(Season.id))) == ids
    assert len(ids) == 8
    for left, right in zip(CANONICAL_SEASONS, CANONICAL_SEASONS[1:]):
        assert left.starts_on.weekday() == 0 and left.ends_on.weekday() == 6
        assert left.ends_on + timedelta(days=1) == right.starts_on
    assert session.get(Season, ids[0]).status == "active"  # Closing still needs finalization.


def test_resolver_expired_future_disabled_and_overlap_fail_closed(database):
    session, _ = database
    bootstrap_canonical_seasons(session)
    current = season_covering_date(session, NOW.date())
    for status in ("planned", "closed"):
        current.status = status
        session.flush()
        assert season_covering_date(session, NOW.date()) is None
    current.status = "active"
    session.add(Season(key="conflicting-season", name="Conflict", starts_on=NOW.date(), status="active"))
    session.flush()
    with pytest.raises(SeasonConfigurationError, match="Overlapping"):
        season_covering_date(session, NOW.date())


def test_dry_run_is_read_only_repair_keeps_week_ids_and_creates_missing_once(database):
    session, _ = database
    source, weeks = legacy_data(session)
    before = [(w.id, w.week_start, w.week_end, w.verification_deadline_at, w.finalize_after) for w in weeks]
    assert not calendar_plan(session)["safe"]  # Safe bootstrap cannot truncate legacy history.
    plan = calendar_plan(session, repair=True)
    assert plan["safe"] and not session.dirty and not session.new
    assert plan["reparent_weeks"] == []
    assert len(plan["create_weeks"]) == 2
    assert source.ends_on is None
    apply_calendar_plan(session, repair=True)
    session.commit()
    assert source.ends_on == date(2026, 9, 13)
    assert [(w.id, w.week_start, w.week_end, w.verification_deadline_at, w.finalize_after) for w in weeks] == before
    assert all(w.season_id == source.id for w in weeks)
    apply_calendar_plan(session, repair=True)
    session.commit()
    assert session.scalar(select(func.count()).select_from(ContestWeek)) == 9
    for week in session.scalars(select(ContestWeek)):
        season = session.get(Season, week.season_id)
        assert season.starts_on <= week.week_start
        assert week.week_end <= season.ends_on + timedelta(days=1)
    assert not calendar_plan(session, repair=True)["reparent_weeks"]


def add_history(session, week):
    student = WoodchuckProfile(woodchuck_id=f"WC-SEASON-HISTORY-{week.id}", display_name="History", pin_hash="hash",
                              instrument="Tuba", level="Beginner", goal="Practice")
    contest = session.scalar(select(Contest).where(Contest.key == "weekly-points-leaders")) or Contest(
        key="weekly-points-leaders", name="Practice", metric_type="practice_minutes", subject_type="student")
    session.add_all([student, contest])
    session.flush()
    snapshot = TeamWeekMembershipSnapshot(contest_week_id=week.id, profile_id=student.id, snapshot_at=NOW)
    result = ContestResult(contest_week_id=week.id, contest_id=contest.id, division="open",
                           subject_type="student", subject_key=str(student.id), profile_id=student.id,
                           display_name_snapshot="History", score=50, rank=1, medal="gold")
    session.add_all([snapshot, result])
    session.flush()
    source = f"contest:{week.id}:weekly-points-leaders:open:student:{student.id}"
    grant = RewardGrant(profile_id=student.id, contest_result_id=result.id, source_key=source,
                        reward_type="crown_win", amount=1, category_key="practice")
    crown = CrownAward(profile_id=student.id, source_key=source, category_key="practice", earned_at=NOW)
    progress = CrownProgress(profile_id=student.id, category_key="practice", qualifying_wins=10, crown_earned_at=NOW)
    session.add_all([grant, crown, progress])
    week.status = "finalized"
    week.finalized_at = NOW
    session.commit()
    return student, snapshot, result, grant, crown, progress


def test_safe_reparent_preserves_frozen_results_snapshots_and_reward_crown_links(database):
    session, _ = database
    # Synthetic out-of-range history, NOT the production weeks 1–7. Keep the
    # existing generic repair/dependency contract covered beyond the new cutoff.
    _, weeks = legacy_data(session, week_count=9)
    history = add_history(session, weeks[7])
    # Every persisted column of dependent history must survive unchanged.
    def columns(row):
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}
    before = [columns(row) for row in history]
    deps = calendar_plan(session, repair=True)["reparent_weeks"][0]["dependencies"]
    assert {key: deps[key] for key in ("contest_results", "membership_snapshots", "reward_grants",
                                     "crown_awards", "recipient_crown_progress")} == {
        "contest_results": 1, "membership_snapshots": 1, "reward_grants": 1,
        "crown_awards": 1, "recipient_crown_progress": 1}
    apply_calendar_plan(session, repair=True)
    session.commit()
    assert [columns(row) for row in history] == before
    assert weeks[7].status == "finalized" and weeks[7].finalized_at == NOW


def test_old_teams_memberships_preserved_and_frozen_old_team_aborts(database):
    session, _ = database
    source, weeks = legacy_data(session, week_count=9)
    student, snapshot, *_ = add_history(session, weeks[7])
    team = make_team(session, season_id=source.id, display_name="Historical team", normalized_name="historical team", emblem_key="emoji:lion")
    session.add(team)
    session.flush()
    member = TeamMembership(season_id=source.id, team_id=team.id, profile_id=student.id,
                            selected_week_start=weeks[0].week_start, started_at=NOW - timedelta(days=45))
    session.add(member)
    session.flush()
    snapshot.team_id, snapshot.membership_id = team.id, member.id
    session.commit()
    with pytest.raises(SeasonConfigurationError, match="frozen team"):
        apply_calendar_plan(session, repair=True)
    session.rollback()
    assert source.ends_on is None and weeks[4].season_id == source.id
    assert session.scalar(select(func.count()).select_from(Season)) == 1
    snapshot.team_id = snapshot.membership_id = None
    session.commit()
    apply_calendar_plan(session, repair=True)
    session.commit()
    assert team.season_id == member.season_id == source.id
    assert member.ended_at is None


def test_apply_rolls_back_partial_work_on_failure(database, monkeypatch):
    session, factory = database
    source, weeks = legacy_data(session)
    def fail(_):
        raise RuntimeError("injected week failure")
    monkeypatch.setattr(season_maintenance, "contest_week_schedule", fail)
    with pytest.raises(RuntimeError, match="injected"):
        with factory.begin() as transaction:
            apply_calendar_plan(transaction, repair=True)
    session.expire_all()
    assert source.ends_on is None
    assert all(w.season_id == source.id for w in weeks)
    assert session.scalar(select(func.count()).select_from(Season)) == 1


def test_rollover_reuses_preseeded_canonical_season_and_weeks(database):
    session, _ = database
    bootstrap_canonical_seasons(session)
    source, _, week = ensure_current_contest_data(session, now=datetime(2026, 8, 18, tzinfo=timezone.utc))
    week.status, week.finalized_at = "finalized", NOW
    target, _, existing_week = ensure_current_contest_data(session, now=BACK_TO_SCHOOL_NOW)
    target_id, week_id = target.id, existing_week.id
    result = rollover_season(session, source_key=source.key, next_key=target.key, next_name=target.name,
                             next_starts_on=target.starts_on, next_ends_on=target.ends_on, now=BACK_TO_SCHOOL_NOW)
    session.commit()
    assert result.weeks_created == 1
    assert target.id == target_id and existing_week.id == week_id
    assert source.status == "closed"
    assert season_covering_date(session, BACK_TO_SCHOOL_NOW.date()).id == target_id


def test_all_consumers_agree_and_board_has_no_calendar(roster_db, monkeypatch):
    profile_id = add_dual_role_student(roster_db, "Canonical Student")
    with roster_db() as session:
        source, _ = legacy_data(session)
        apply_calendar_plan(session, repair=True)
        session.commit()
        season, week = current_roster_period(session, today=NOW.date())
        assert season.key == "band-camp-2026"
        assert week.week_start == date(2026, 9, 7)
        assert verifier_dashboard_snapshot(session, verifier_id=2, today=NOW.date())["student"]["season"]["name"] == season.name
        assert dashboard_metrics(session, verifier_id=1, today=NOW.date())["students"][0]["team"] is None
        assert band_director_practice_students(session, verifier_id=1, today=NOW.date())[0]["contest"]["week_start"] == week.week_start.isoformat()
        assert current_contests_payload(session, now=NOW, current_profile_id=profile_id)["season"]["key"] == season.key
        student = session.get(WoodchuckProfile, profile_id)
        assert selection_payload(session, profile=student, now=NOW)["season"]["key"] == season.key
        assert admin_status(session, now=NOW)["active_season"]["key"] == season.key
        assert season_status_payload(session, now=NOW)["rollover_source"]["key"] == source.key
        assert board_season_presentation(season).key == season.key
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(main, "datetime", Clock)
    response = signed_client().get("/quest")
    assert response.status_code == 200
    assert response.text.count('aria-label="Band Camp"') == 1
    assert 'Band Camp Standings' in response.text
    assert set(f.name for f in fields(BoardSeason)) == {"key", "title"}
    # Presentation follows the DB even when it differs from bootstrap metadata.
    with roster_db() as session:
        season_covering_date(session, NOW.date()).name = "Database Season Name"
        session.commit()
    response = signed_client().get("/quest")
    assert 'aria-label="Database Season Name"' in response.text
    assert 'Database Season Name Standings' in response.text
    assert 'Loading Database Season Name standings' in response.text


def test_current_membership_helpers_ignore_expired_and_future_teams(roster_db):
    profile_id = add_dual_role_student(roster_db, "Seasonal Member")
    with roster_db() as session:
        bootstrap_canonical_seasons(session)
        target = season_covering_date(session, NOW.date())
        teams = {}
        for season in session.scalars(select(Season)):
            team = make_team(session, season_id=season.id, display_name=season.name, normalized_name="shared identity", emblem_key="emoji:lion")
            session.add(team)
            session.flush()
            session.add(TeamMembership(season_id=season.id, team_id=team.id, profile_id=profile_id,
                                       selected_week_start=season.starts_on,
                                       started_at=datetime(2026, 7, 28, tzinfo=timezone.utc)))
            teams[season.id] = team
        session.commit()
        assert _active_team_id_for_event(session, profile_id, NOW) == teams[target.id].id
        champion = {"_family_id": teams[target.id].family_id}
        assert _current_team_member_ids(session, champion, now=NOW) == {profile_id}
        assert dashboard_metrics(session, verifier_id=1, today=NOW.date())["students"][0]["team"]["name"] == "Band Camp"
        assert verifier_dashboard_snapshot(session, verifier_id=2, today=NOW.date())["student"]["team"]["name"] == "Band Camp"
        target.status = "closed"
        session.flush()
        assert _active_team_id_for_event(session, profile_id, NOW) is None
        assert _current_team_member_ids(session, champion, now=NOW) == set()


def test_existing_durable_dates_and_names_are_runtime_truth(database):
    session, _ = database
    custom = Season(key="custom-season", name="Durable custom name", starts_on=date(2026, 9, 7),
                    ends_on=date(2026, 9, 13), status="active")
    session.add(custom)
    session.commit()
    season, _, _ = ensure_current_contest_data(session, now=NOW)
    assert season.id == custom.id
    assert board_season_presentation(season).title == custom.name
    assert current_roster_period(session, today=NOW.date())[0].id == custom.id
    assert session.scalar(select(func.count()).select_from(Season)) == 1


def test_lazy_bootstrap_later_season(database):
    session, _ = database
    season, _, _ = ensure_current_contest_data(session, now=BACK_TO_SCHOOL_NOW)
    assert season.key == "back-to-school-2026"
    assert session.scalar(select(func.count()).select_from(Season)) == 1
    halloween = datetime(2026, 9, 28, 18, tzinfo=timezone.utc)
    assert ensure_current_contest_data(session, now=halloween)[0].key == "halloween-2026"


def test_admin_expired_source_is_not_current_and_future_week_not_selected(database):
    session, _ = database
    source, weeks = legacy_data(session)
    apply_calendar_plan(session, repair=True)
    session.commit()
    status = admin_status(session, now=BACK_TO_SCHOOL_NOW)
    assert status["active_season"]["key"] == "back-to-school-2026"
    assert status["current_week"]["week_start"] == "2026-09-14"
    assert status["rollover_source"]["key"] == source.key


@pytest.mark.parametrize("problem", ["duplicate", "partial_week", "unknown_result", "overlap"])
def test_ambiguous_repair_is_read_only_and_aborts(database, problem):
    session, _ = database
    source, weeks = legacy_data(session, week_count=9)
    if problem == "duplicate":
        end, deadline, finalizes = contest_week_schedule(weeks[4].week_start)
        target = Season(key="back-to-school-2026", name="Back to School", starts_on=date(2026, 9, 14),
                        ends_on=date(2026, 9, 27), status="active")
        session.add(target)
        session.flush()
        session.add(ContestWeek(season_id=target.id, week_start=weeks[4].week_start, week_end=end,
                                status="open", verification_deadline_at=deadline, finalize_after=finalizes))
    elif problem == "partial_week":
        weeks[4].week_end -= timedelta(days=1)
    elif problem == "unknown_result":
        _, _, result, *_ = add_history(session, weeks[7])
        session.get(Contest, result.contest_id).key = "unknown-season-score"
    else:
        session.add(Season(key="conflicting-season", name="Conflict", starts_on=NOW.date(), status="active"))
    session.commit()
    plan = calendar_plan(session, repair=True)
    assert not plan["safe"] and not session.dirty and not session.new
    with pytest.raises(SeasonConfigurationError, match="blocked"):
        apply_calendar_plan(session, repair=True)
    session.rollback()
    assert source.ends_on is None and weeks[4].season_id == source.id


def test_normal_bootstrap_refuses_to_recreate_misowned_week(database):
    session, _ = database
    source, weeks = legacy_data(session, week_count=9)
    source.ends_on = date(2026, 9, 13)
    session.commit()
    with pytest.raises(HTTPException) as error:
        ensure_current_contest_data(session, now=BACK_TO_SCHOOL_NOW)
    assert error.value.status_code == 409
    assert "explicit repair" in error.value.detail
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Season)) == 1
    assert session.scalar(select(func.count()).select_from(ContestWeek)) == 9
    assert weeks[6].season_id == source.id


def test_production_transition_preserves_all_accumulated_history(database, monkeypatch, capsys):
    session, factory = database
    source, weeks = legacy_data(session)
    assert [week.id for week in weeks] == list(range(1, 8))
    team = make_team(session, season_id=source.id, display_name="Band Camp History", normalized_name="band camp history",
                emblem_key="emoji:lion")
    team_contest = Contest(key="team-weekly-practice", name="Team Practice",
                           metric_type="practice_minutes", subject_type="team")
    session.add_all([team, team_contest])
    session.flush()
    for week in weeks[4:6]:
        student, snapshot, _, _, _, _ = add_history(session, week)
        member = TeamMembership(season_id=source.id, team_id=team.id, profile_id=student.id,
                                selected_week_start=weeks[0].week_start,
                                started_at=datetime(2026, 7, 28, tzinfo=timezone.utc))
        session.add(member)
        session.flush()
        snapshot.team_id, snapshot.membership_id = team.id, member.id
        result = ContestResult(contest_week_id=week.id, contest_id=team_contest.id, division="open",
                               subject_type="team", subject_key=str(team.id), team_id=team.id,
                               display_name_snapshot=team.display_name, score=50, rank=1, medal="gold")
        session.add(result)
        session.flush()
        session.add(RewardGrant(profile_id=student.id, contest_result_id=result.id,
                                source_key=f"contest:{week.id}:team-win", reward_type="trophy", amount=1))
        session.add(CampPointAward(profile_id=student.id, team_id=team.id, activity_type="contest-placement",
                                  points_awarded=3, occurred_at=NOW,
                                  duplicate_key=f"contest:{week.id}:team-win:camp-points"))
        session.add(PracticeChart(profile_id=student.id, team_id=team.id, practice_date=week.week_start,
                                  minutes=50, instrument="Tuba", source="p-book", include_team_contests=True))
    session.commit()

    def history_state():
        # Query persisted rows, not cached ORM objects: every historical column,
        # including IDs, ownership, timestamps, scores and source keys must match.
        saved = {}
        for table in Base.metadata.sorted_tables:
            if table.name == "seasons":
                continue
            query = select(table).order_by(*table.primary_key.columns)
            if table.name == "contest_weeks":
                query = query.where(table.c.id <= 7)
            saved[table.name] = session.execute(query).all()
        return saved

    before = history_state()
    monkeypatch.setattr(season_maintenance, "SessionLocal", factory)
    assert season_maintenance.main(["repair"]) == 0  # CLI is dry-run by default.
    import json
    plan = json.loads(capsys.readouterr().out)
    assert plan["applied"] is False and plan["safe"] is True and plan["blockers"] == []
    assert plan["reparent_weeks"] == []
    assert plan["update_seasons"] == [{"id": source.id, "key": source.key, "ends_on": "2026-09-13"}]
    assert "back-to-school-2026" in plan["create_seasons"]
    assert plan["create_weeks"] == [
        {"season": "back-to-school-2026", "start": "2026-09-14"},
        {"season": "back-to-school-2026", "start": "2026-09-21"},
    ]
    assert history_state() == before and source.ends_on is None
    assert plan["preserved_band_camp"]["teams"] == 1
    assert plan["preserved_band_camp"]["team_memberships"] == 2
    for _ in range(2):
        apply_calendar_plan(session, repair=True)
        session.commit()
        session.expire_all()
        assert history_state() == before
        assert all(week.season_id == source.id for week in weeks)
        assert [week.status for week in weeks[4:]] == ["finalized", "finalized", "open"]
        assert session.scalar(select(func.count()).select_from(ContestWeek)) == 9
        assert session.scalar(select(func.count()).select_from(Season)) == 8
    target = season_covering_date(session, date(2026, 9, 14))
    assert (target.key, target.starts_on, target.ends_on) == (
        "back-to-school-2026", date(2026, 9, 14), date(2026, 9, 27))
    assert [(week.week_start, week.week_end) for week in session.scalars(
        select(ContestWeek).where(ContestWeek.season_id == target.id).order_by(ContestWeek.week_start))] == [
            (date(2026, 9, 14), date(2026, 9, 21)), (date(2026, 9, 21), date(2026, 9, 28))]
    repeat = calendar_plan(session, repair=True)
    assert repeat["safe"] and all(not repeat[key] for key in (
        "reparent_weeks", "update_seasons", "create_seasons", "create_weeks"))


def test_previous_calendar_repair_is_not_silently_rewritten(database):
    session, _ = database
    source, weeks = legacy_data(session)
    source.ends_on = date(2026, 8, 23)  # Superseded proposal, not an open-ended source.
    session.commit()
    assert not calendar_plan(session, repair=True)["safe"]
    with pytest.raises(SeasonConfigurationError, match="conflicts"):
        apply_calendar_plan(session, repair=True)
    session.rollback()
    assert source.ends_on == date(2026, 8, 23)
    assert all(week.season_id == source.id for week in weeks)


@pytest.mark.parametrize("now,name", [(NOW, "Band Camp"), (BACK_TO_SCHOOL_NOW, "Back to School")])
def test_runtime_consumers_follow_launch_transition(roster_db, now, name):
    profile_id = add_dual_role_student(roster_db, "Launch Student")
    with roster_db() as session:
        legacy_data(session)
        apply_calendar_plan(session, repair=True)
        session.commit()
        season, _ = current_roster_period(session, today=now.date())
        assert season.name == name
        assert board_season_presentation(season).title == name
        assert verifier_dashboard_snapshot(session, verifier_id=2, today=now.date())["student"]["season"]["name"] == name
        assert current_contests_payload(session, now=now, current_profile_id=profile_id)["season"]["name"] == name
        assert selection_payload(session, profile=session.get(WoodchuckProfile, profile_id), now=now)["season"]["name"] == name
        assert admin_status(session, now=now)["active_season"]["name"] == name
