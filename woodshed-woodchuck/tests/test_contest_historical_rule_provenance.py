"""Frozen contest snapshots need rule provenance before automatic repair."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.contest_jobs import audit_or_repair_history
from app.contests import (
    FINALIZER_RULES_VERSION,
    _charts_and_approved_ids,
    ensure_current_contest_data,
    finalize_contest_week,
    team_leaderboards,
)
from app.db import Base
from app.models import (
    CampPointAward,
    Contest,
    ContestResult,
    ContestWeek,
    PracticeChart,
    PracticeChartVerification,
    Season,
    Team,
    TeamFamily,
    WoodchuckProfile,
)
from app.seasons import bootstrap_canonical_seasons


JULY = datetime(2026, 7, 28, 15, tzinfo=timezone.utc)
SEPTEMBER = datetime(2026, 9, 22, 15, tzinfo=timezone.utc)


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def postgres_database(tmp_path):
    from tests.test_team_families import disposable_url

    engine = create_engine(disposable_url(tmp_path, "postgresql"))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _profile(session: Session, label: str) -> WoodchuckProfile:
    person = WoodchuckProfile(
        woodchuck_id=f"WC-RULES-{label}", display_name=label,
        pin_hash="test", instrument="Flute", level="Beginner", goal="Practice",
    )
    session.add(person)
    session.flush()
    return person


def _team(session: Session, season: Season, label: str, *, family: TeamFamily | None = None) -> Team:
    if family is None:
        family = TeamFamily()
        session.add(family)
        session.flush()
    team = Team(
        season_id=season.id, family_id=family.id,
        display_name=label, normalized_name=label.casefold(),
        emblem_key=f"letter:{family.id}",
    )
    session.add(team)
    session.flush()
    return team


def _chart(
    session: Session, person: WoodchuckProfile, *, practice_date,
    created_at: datetime, minutes: int, team: Team | None = None,
    approved: bool,
) -> PracticeChart:
    chart = PracticeChart(
        profile_id=person.id, practice_date=practice_date,
        minutes=minutes, instrument=person.instrument, practice_details=[],
        source="p-book", credits_awarded=0, include_contests=True,
        include_team_contests=team is not None,
        team_id=team.id if team is not None else None,
        created_at=created_at,
    )
    session.add(chart)
    session.flush()
    if approved:
        session.add(PracticeChartVerification(
            practice_chart_id=chart.id, status="approved",
            responded_at=created_at + timedelta(hours=1),
        ))
    return chart


def _old_result(session: Session, week, contest: Contest, subject, *, score: int, rank: int):
    is_team = isinstance(subject, Team)
    row = ContestResult(
        contest_week_id=week.id, contest_id=contest.id, division="open",
        subject_type="team" if is_team else "student",
        subject_key=str(subject.id),
        team_id=subject.id if is_team else None,
        profile_id=None if is_team else subject.id,
        display_name_snapshot=subject.display_name,
        score=score, rank=rank,
        medal={1: "gold", 2: "silver", 3: "bronze"}[rank],
    )
    session.add(row)
    return row


def _freeze_old_week(session: Session, week) -> datetime:
    finalized_at = week.finalize_after + timedelta(minutes=1)
    week.status = "finalized"
    week.finalized_at = finalized_at
    week.practice_scoring_mode = "legacy_minutes"
    assert week.finalizer_rules_version is None
    session.commit()
    return finalized_at


def _snapshot(session: Session):
    return deepcopy({
        table.name: [tuple(row) for row in session.execute(
            select(*table.columns).order_by(*table.primary_key.columns)
        )]
        for table in Base.metadata.sorted_tables
    })


def _assert_unknown_history_refused(
    session: Session, week, finalized_at: datetime,
    reason: str = "historical_rules_unknown",
    direct_match: str | None = None,
):
    before = _snapshot(session)
    now = finalized_at + timedelta(days=1)
    dry_run = audit_or_repair_history(
        session, week_start=week.week_start, now=now,
    )
    assert dry_run["action"] == "manual_review"
    assert dry_run["reason"] == reason
    assert dry_run["before"] == dry_run["after"]
    assert all(value == 0 for value in dry_run["created"].values())
    assert _snapshot(session) == before

    statements = []
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().upper())
    event.listen(session.get_bind(), "before_cursor_execute", record)
    try:
        applied = audit_or_repair_history(
            session, week_start=week.week_start, now=now, apply=True,
        )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", record)
    assert applied["action"] == "manual_review"
    assert applied["reason"] == reason
    assert applied["before"] == applied["after"]
    assert all(value == 0 for value in applied["created"].values())
    assert not any(sql.startswith(("INSERT ", "UPDATE ", "DELETE ")) for sql in statements)
    session.commit()
    assert _snapshot(session) == before

    with pytest.raises(HTTPException, match=direct_match or reason) as error:
        finalize_contest_week(
            session, week_start=week.week_start, now=now, repair_finalized=True,
        )
    assert error.value.status_code == 409
    assert _snapshot(session) == before


def test_old_unverified_practice_cannot_gain_a_current_rules_result(database):
    session = database
    _season, contests, week = ensure_current_contest_data(session, now=JULY)
    contest = next(row for row in contests if row.key == "weekly-points-leaders")
    people = [_profile(session, label) for label in ("Old Gold", "Silver", "Bronze", "Fourth")]
    charts = [
        _chart(session, person, practice_date=week.week_start + timedelta(days=1),
               created_at=JULY, minutes=minutes, approved=index != 0)
        for index, (person, minutes) in enumerate(zip(people, (81, 70, 60, 50)))
    ]
    for rank, score in enumerate((81, 70, 60), start=1):
        _old_result(session, week, contest, people[rank - 1], score=score, rank=rank)
    finalized_at = _freeze_old_week(session, week)

    reports, approved, _pristine = _charts_and_approved_ids(
        session, week, submitted_before=finalized_at,
    )
    # Current reporting includes unverified BOOK charts, but cannot rewrite
    # finalized history whose original finalizer semantics are unknown.
    assert {chart.id for chart in reports} == {chart.id for chart in charts}
    assert approved == {chart.id for chart in charts[1:]}
    assert session.scalar(select(ContestResult).where(
        ContestResult.subject_key == str(people[3].id),
    )) is None
    _assert_unknown_history_refused(session, week, finalized_at)


def test_old_self_reported_board_awards_cannot_create_trivia_only_medal(database):
    session = database
    season, contests, week = ensure_current_contest_data(session, now=JULY)
    contest = next(row for row in contests if row.key == "team-weekly-activity-points")
    teams = [_team(session, season, label) for label in
             ("Union", "Teachers", "Bronze Band", "Trivia Only")]
    people = [_profile(session, str(index)) for index in range(4)]
    ledger = (
        (("hours", 3), ("care", 3), ("marching", 2), ("trivia", 4)),
        (("hours", 2), ("care", 2), ("marching", 1), ("trivia", 3)),
        (("hours", 2), ("care", 2), ("marching", 2), ("trivia", 1)),
        (("trivia", 5),),
    )
    for index, (person, team, awards) in enumerate(zip(people, teams, ledger)):
        for award_index, (activity, points) in enumerate(awards):
            session.add(CampPointAward(
                profile_id=person.id, team_id=team.id,
                activity_type=activity, points_awarded=points,
                occurred_at=JULY, created_at=JULY,
                duplicate_key=f"old-ledger:{index}:{award_index}",
            ))
    for rank, score in enumerate((12, 8, 7), start=1):
        _old_result(session, week, contest, teams[rank - 1], score=score, rank=rank)
    finalized_at = _freeze_old_week(session, week)

    current = team_leaderboards(
        session, season=season, contest_week=week,
        source_cutoff=finalized_at, _include_private=True,
    )["team-weekly-activity-points"]["open"]
    assert [(row["team_id"], row["score"]) for row in current] == [
        (teams[3].id, 5), (teams[0].id, 4),
        (teams[1].id, 3), (teams[2].id, 1),
    ]
    assert session.scalar(select(ContestResult).where(
        ContestResult.subject_key == str(teams[3].id),
    )) is None
    _assert_unknown_history_refused(session, week, finalized_at)


def test_old_seasonal_team_results_cannot_gain_family_continuity_medal(database):
    session = database
    bootstrap_canonical_seasons(session)
    session.commit()
    season, contests, week = ensure_current_contest_data(session, now=SEPTEMBER)
    source_season = session.scalar(select(Season).where(Season.key == "band-camp-2026"))
    contest = next(row for row in contests if row.key == "team-lifetime-practice")
    source_team = _team(session, source_season, "Carryover")
    carried_team = _team(session, season, "Carryover", family=session.get(TeamFamily, source_team.family_id))
    current_teams = [_team(session, season, label) for label in
                     ("Gold Now", "Silver Now", "Bronze Now")]
    people = [_profile(session, str(index)) for index in range(4)]
    _chart(session, people[0], practice_date=source_season.ends_on - timedelta(days=3),
           created_at=datetime(2026, 9, 10, 15, tzinfo=timezone.utc),
           minutes=100, team=source_team, approved=True)
    for person, team, minutes in zip(people[1:], current_teams, (60, 50, 40)):
        _chart(session, person, practice_date=week.week_start + timedelta(days=1),
               created_at=SEPTEMBER, minutes=minutes, team=team, approved=True)
    for rank, score in enumerate((60, 50, 40), start=1):
        _old_result(session, week, contest, current_teams[rank - 1], score=score, rank=rank)
    finalized_at = _freeze_old_week(session, week)

    current = team_leaderboards(
        session, season=season, contest_week=week,
        source_cutoff=finalized_at, _include_private=True,
    )["team-lifetime-practice"]["open"]
    assert current[0]["team_id"] == carried_team.id
    assert current[0]["score"] == 100
    assert session.scalar(select(ContestResult).where(
        ContestResult.subject_key == str(carried_team.id),
    )) is None
    _assert_unknown_history_refused(session, week, finalized_at)


def test_compatible_finalized_week_can_recreate_one_missing_result(database):
    session = database
    _season, _contests, week = ensure_current_contest_data(session, now=JULY)
    person = _profile(session, "Compatible")
    _chart(session, person, practice_date=week.week_start + timedelta(days=1),
           created_at=JULY, minutes=40, approved=True)
    session.commit()
    final_at = week.finalize_after + timedelta(minutes=1)
    finalize_contest_week(session, week_start=week.week_start, now=final_at)
    session.commit()
    assert week.finalizer_rules_version == FINALIZER_RULES_VERSION
    result = session.scalar(select(ContestResult).join(Contest).where(
        ContestResult.contest_week_id == week.id,
        Contest.key == "weekly-practice-by-instrument",
    ))
    assert result is not None
    session.delete(result)
    session.commit()
    before = _snapshot(session)

    preview = audit_or_repair_history(
        session, week_start=week.week_start, now=final_at + timedelta(days=1),
    )
    assert preview["action"] == "repaired"
    assert preview["created"]["results"] == 1
    assert _snapshot(session) == before
    applied = audit_or_repair_history(
        session, week_start=week.week_start, now=final_at + timedelta(days=1),
        apply=True,
    )
    session.commit()
    assert applied["action"] == "repaired"
    assert applied["created"]["results"] == 1
    assert session.scalar(select(ContestResult).join(Contest).where(
        ContestResult.contest_week_id == week.id,
        Contest.key == "weekly-practice-by-instrument",
    )) is not None


def test_mismatched_rules_version_blocks_a_missing_result(database):
    session = database
    _season, _contests, week = ensure_current_contest_data(session, now=JULY)
    person = _profile(session, "Other Rules")
    _chart(session, person, practice_date=week.week_start + timedelta(days=1),
           created_at=JULY, minutes=40, approved=True)
    session.commit()
    final_at = week.finalize_after + timedelta(minutes=1)
    finalize_contest_week(session, week_start=week.week_start, now=final_at)
    session.commit()
    missing = session.scalar(select(ContestResult).join(Contest).where(
        ContestResult.contest_week_id == week.id,
        Contest.key == "weekly-practice-by-instrument",
    ))
    assert missing is not None
    session.delete(missing)
    week.finalizer_rules_version = "earlier_finalizer_rules"
    session.commit()

    _assert_unknown_history_refused(
        session, week, final_at, reason="historical_rules_incompatible",
    )


def test_changed_persisted_contest_settings_block_repair(database):
    session = database
    _season, _contests, week = ensure_current_contest_data(session, now=JULY)
    person = _profile(session, "Definitions Changed")
    _chart(session, person, practice_date=week.week_start + timedelta(days=1),
           created_at=JULY, minutes=40, approved=True)
    session.commit()
    final_at = week.finalize_after + timedelta(minutes=1)
    finalize_contest_week(session, week_start=week.week_start, now=final_at)
    session.commit()
    missing = session.scalar(select(ContestResult).join(Contest).where(
        ContestResult.contest_week_id == week.id,
        Contest.key == "weekly-practice-by-instrument",
    ))
    assert missing is not None
    session.delete(missing)
    definition = session.scalar(select(Contest).where(
        Contest.key == "weekly-points-leaders",
    ))
    assert definition is not None
    definition.crown_category = "changed-after-finalization"
    session.commit()

    _assert_unknown_history_refused(
        session, week, final_at,
        reason="historical_contest_definitions_incompatible",
        direct_match="contest definitions conflict",
    )


@pytest.mark.parametrize("damage,reason", [
    ("missing_mode", "historical_practice_scoring_unknown"),
    ("conflicting_score", "historical_practice_scoring_conflict"),
    ("missing_cutoff", "historical_finalization_cutoff_unknown"),
])
def test_audit_reports_other_unknown_provenance_as_manual_review(database, damage, reason):
    session = database
    _season, _contests, week = ensure_current_contest_data(session, now=JULY)
    person = _profile(session, "Scoring Provenance")
    _chart(session, person, practice_date=week.week_start + timedelta(days=1),
           created_at=JULY, minutes=40, approved=True)
    session.commit()
    final_at = week.finalize_after + timedelta(minutes=1)
    finalize_contest_week(session, week_start=week.week_start, now=final_at)
    session.commit()
    assert week.finalizer_rules_version == FINALIZER_RULES_VERSION
    if damage == "missing_mode":
        week.practice_scoring_mode = None
    elif damage == "missing_cutoff":
        week.finalized_at = None
    else:
        result = session.scalar(select(ContestResult).join(Contest).where(
            ContestResult.contest_week_id == week.id,
            Contest.metric_type == "practice_minutes",
        ))
        assert result is not None
        result.precise_score = None
    session.commit()
    before = _snapshot(session)

    for apply in (False, True):
        report = audit_or_repair_history(
            session, week_start=week.week_start,
            now=final_at + timedelta(days=1), apply=apply,
        )
        assert report["action"] == "manual_review"
        assert report["reason"] == reason
        assert report["before"] == report["after"]
        assert all(value == 0 for value in report["created"].values())
        session.commit()
        assert _snapshot(session) == before


def test_postgres_dry_run_and_apply_respect_rule_provenance(postgres_database):
    session = postgres_database
    _season, _contests, week = ensure_current_contest_data(session, now=JULY)
    person = _profile(session, "PG")
    _chart(session, person, practice_date=week.week_start + timedelta(days=1),
           created_at=JULY, minutes=40, approved=True)
    session.commit()
    final_at = week.finalize_after + timedelta(minutes=1)
    finalize_contest_week(session, week_start=week.week_start, now=final_at)
    session.commit()
    assert week.status == "finalized"
    assert week.finalizer_rules_version == FINALIZER_RULES_VERSION
    missing = session.scalar(select(ContestResult).join(Contest).where(
        ContestResult.contest_week_id == week.id,
        Contest.key == "weekly-practice-by-instrument",
    ))
    assert missing is not None
    session.delete(missing)
    session.commit()
    before = _snapshot(session)

    preview = audit_or_repair_history(
        session, week_start=week.week_start, now=final_at + timedelta(days=1),
    )
    assert preview["action"] == "repaired"
    assert preview["created"]["results"] == 1
    assert _snapshot(session) == before
    applied = audit_or_repair_history(
        session, week_start=week.week_start,
        now=final_at + timedelta(days=1), apply=True,
    )
    session.commit()
    assert applied["action"] == "repaired"
    assert applied["created"]["results"] == 1

    # A subsequent unknown marker must block the same repair path on PostgreSQL.
    again = session.scalar(select(ContestResult).join(Contest).where(
        ContestResult.contest_week_id == week.id,
        Contest.key == "weekly-practice-by-instrument",
    ))
    session.delete(again)
    week.finalizer_rules_version = None
    session.commit()
    _assert_unknown_history_refused(session, week, final_at)


def _stale_open_week_after_old_writer(session: Session):
    _season, contests, week = ensure_current_contest_data(session, now=JULY)
    person = _profile(session, "Race")
    _chart(session, person, practice_date=week.week_start + timedelta(days=1),
           created_at=JULY, minutes=40, approved=False)
    contest = next(row for row in contests if row.key == "weekly-points-leaders")
    _old_result(session, week, contest, person, score=40, rank=1)
    session.commit()
    session.expire_all()

    cached = session.get(ContestWeek, week.id)
    assert cached.status == "open"
    final_at = cached.finalize_after + timedelta(minutes=1)
    with Session(session.get_bind(), expire_on_commit=False) as old_writer:
        durable = old_writer.get(ContestWeek, week.id)
        durable.status = "finalized"
        durable.finalized_at = final_at
        durable.practice_scoring_mode = "legacy_minutes"
        old_writer.commit()
    assert cached.status == "open"  # SQLAlchemy has not refreshed Session A yet.
    assert cached.finalizer_rules_version is None
    return cached, final_at


@pytest.mark.parametrize("apply", [False, True])
def test_postgres_audit_refreshes_stale_open_week_before_repair(postgres_database, apply):
    session = postgres_database
    week, final_at = _stale_open_week_after_old_writer(session)
    before = _snapshot(session)
    writes = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            writes.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", record)
    try:
        report = audit_or_repair_history(
            session, week_start=week.week_start, now=final_at + timedelta(days=1),
            apply=apply,
        )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", record)
    assert report["action"] == "manual_review"
    assert report["reason"] == "historical_rules_unknown"
    assert report["before"] == report["after"]
    assert all(value == 0 for value in report["created"].values())
    assert writes == []
    assert week.status == "finalized"
    assert week.finalizer_rules_version is None
    assert week.practice_scoring_mode == "legacy_minutes"
    session.commit()
    assert _snapshot(session) == before


def test_postgres_direct_repair_refreshes_stale_open_week(postgres_database):
    session = postgres_database
    week, final_at = _stale_open_week_after_old_writer(session)
    before = _snapshot(session)
    writes = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            writes.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", record)
    try:
        with pytest.raises(HTTPException, match="historical_rules_unknown") as error:
            finalize_contest_week(
                session, week_start=week.week_start,
                now=final_at + timedelta(days=1), repair_finalized=True,
            )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", record)
    assert error.value.status_code == 409
    assert writes == []
    assert week.status == "finalized"
    assert week.finalizer_rules_version is None
    assert week.practice_scoring_mode == "legacy_minutes"
    session.rollback()
    assert _snapshot(session) == before
