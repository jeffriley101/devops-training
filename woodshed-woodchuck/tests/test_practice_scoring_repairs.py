"""Finalization mode survives missing results; repair never guesses it."""
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.contests import (
    LEGACY_PRACTICE_SCORING, PRECISE_PRACTICE_SCORING,
    _scoring_seconds, _weekly_team_scores, ensure_band_camp_data, finalize_contest_week,
)
from app.models import (
    CampPointAward, Contest, ContestResult, ContestWeek, CrownAward, CrownProgress,
    PracticeChart, RewardGrant, TeamWeekMembershipSnapshot, WoodchuckState,
)
from app.teams import create_and_join_team, select_team
from test_pristine_practice import pristine_database, add_profile, NOW, FINAL_NOW

HISTORY = (RewardGrant, CampPointAward, CrownAward, CrownProgress,
           TeamWeekMembershipSnapshot, WoodchuckState)


def snapshot(session, models=HISTORY):
    return deepcopy({model.__tablename__: [tuple(row) for row in session.execute(
        select(*model.__table__.columns).order_by(*model.__table__.primary_key.columns))]
        for model in models})


def seed(session, *, base=0):
    season, _, week = ensure_band_camp_data(session, now=NOW)
    people, teams, charts = [], [], []
    for name, seconds in (("A", base + 119), ("B", base + 61)):
        person = add_profile(session, name)
        session.commit()
        team, _ = create_and_join_team(session, profile=person, season=season,
            name=f"Precision {name}", emblem_key=f"letter:{name}", now=NOW)
        chart = PracticeChart(profile_id=person.id, practice_date=week.week_start,
            minutes=seconds // 60, instrument="Flute", source="pristine",
            detected_playing_seconds=seconds, team_id=team.id, created_at=NOW)
        session.add(chart)
        people.append(person)
        teams.append(team)
        charts.append(chart)
    session.commit()
    return season, week, people, teams, charts


@pytest.mark.parametrize("mode", [LEGACY_PRACTICE_SCORING, PRECISE_PRACTICE_SCORING])
@pytest.mark.parametrize("contest_key,base", [
    ("weekly-points-leaders", 0), ("team-weekly-practice", 0),
    ("team-weekly-average-practice", 240), ("team-lifetime-practice", 0),
])
def test_missing_result_repair_retains_mode_and_cannot_issue_wrong_rewards(
    pristine_database, mode, contest_key, base,
):
    with pristine_database() as session:
        _, week, people, teams, charts = seed(session, base=base)
        if mode == LEGACY_PRACTICE_SCORING:
            # A pre-migration finalized week with incomplete history, carrying
            # the migration's attestation (not inferred from remaining results).
            week.status = "finalized"
            week.finalized_at = FINAL_NOW
            week.practice_scoring_mode = mode
            session.commit()
        else:
            assert [_scoring_seconds(chart, week) for chart in charts] == [base + 119, base + 61]
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW,
                              repair_finalized=mode == LEGACY_PRACTICE_SCORING)
        session.commit()
        session.expire_all()  # Must work from persisted data, not transient state.
        assert week.practice_scoring_mode == mode
        assert [_scoring_seconds(chart, week) for chart in charts] == (
            [base + 60, base + 60] if mode == LEGACY_PRACTICE_SCORING else [base + 119, base + 61])
        contest = session.scalar(select(Contest).where(Contest.key == contest_key))
        subject = str(people[1].id if contest_key == "weekly-points-leaders" else teams[1].id)
        missing = session.scalar(select(ContestResult).where(
            ContestResult.contest_week_id == week.id, ContestResult.contest_id == contest.id,
            ContestResult.subject_key == subject, ContestResult.division == "open"))
        expected = (1, (base + 60) / 60) if mode == LEGACY_PRACTICE_SCORING else (2, (base + 61) / 60)
        assert (missing.rank, missing.effective_score) == expected
        # Leave the original grants in place: a mistakenly rounded rank would
        # create a new rank-specific payout and possibly an undeserved crown win.
        session.delete(missing)
        session.commit()
        surviving = snapshot(session, (ContestResult,))
        history = snapshot(session)
        week_before = snapshot(session, (ContestWeek,))
        finalize_contest_week(session, week_start=week.week_start,
            now=FINAL_NOW + timedelta(days=1), repair_finalized=True)
        session.commit()
        repaired = session.scalar(select(ContestResult).where(
            ContestResult.contest_week_id == week.id, ContestResult.contest_id == contest.id,
            ContestResult.subject_key == subject, ContestResult.division == "open"))
        assert (repaired.rank, repaired.effective_score) == expected
        assert (repaired.precise_score is not None) == (mode == PRECISE_PRACTICE_SCORING)
        after = snapshot(session, (ContestResult,))["contest_results"]
        assert all(row in after for row in surviving["contest_results"])
        assert snapshot(session) == history
        assert snapshot(session, (ContestWeek,)) == week_before
        complete = snapshot(session, (ContestResult, *HISTORY, ContestWeek))
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW + timedelta(days=2))
        assert snapshot(session, (ContestResult, *HISTORY, ContestWeek)) == complete


@pytest.mark.parametrize("mode,expected", [(LEGACY_PRACTICE_SCORING, 6), (PRECISE_PRACTICE_SCORING, 5.5)])
def test_average_rounding_depends_on_original_mode(pristine_database, mode, expected):
    with pristine_database() as session:
        season, week, _, teams, charts = seed(session, base=240)
        charts[0].detected_playing_seconds = 300
        charts[0].minutes = 5
        third = add_profile(session, "C")
        session.commit()
        select_team(session, profile=third, season=season, team=teams[0], now=NOW)
        session.add(PracticeChart(profile_id=third.id, practice_date=week.week_start,
            minutes=6, instrument="Flute", team_id=teams[0].id, created_at=NOW))
        week.status = "finalized"
        week.finalized_at = FINAL_NOW
        week.practice_scoring_mode = mode
        session.commit()
        scores = _weekly_team_scores(session, week, source_cutoff=FINAL_NOW)
        assert scores["open"]["averages"][teams[0].id] == expected
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW, repair_finalized=True)
        contest = session.scalar(select(Contest).where(Contest.key == "team-weekly-average-practice"))
        row = session.scalar(select(ContestResult).where(ContestResult.contest_id == contest.id,
            ContestResult.subject_key == str(teams[0].id), ContestResult.division == "open"))
        assert row.effective_score == expected


@pytest.mark.parametrize("evidence", ["none", "partial_legacy", "partial_precise", "mixed", "all_precise"])
def test_unknown_mode_refuses_even_with_surviving_results(pristine_database, evidence):
    with pristine_database() as session:
        _, week, _, _, _ = seed(session)
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()
        rows = session.scalars(select(ContestResult).join(Contest).where(
            Contest.metric_type == "practice_minutes")).all()
        if evidence != "all_precise":
            keep = 0 if evidence == "none" else 2 if evidence == "mixed" else 1
            for row in rows[keep:]:
                session.delete(row)
            if evidence in {"partial_legacy", "mixed"}:
                rows[0].precise_score = None
        week.practice_scoring_mode = None
        session.commit()
        before = snapshot(session, (ContestResult, *HISTORY, ContestWeek))
        with pytest.raises(HTTPException, match="original practice scoring mode is unknown") as error:
            finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW, repair_finalized=True)
        assert error.value.status_code == 409
        assert snapshot(session, (ContestResult, *HISTORY, ContestWeek)) == before
        # Ordinary repeated finalization is a no-op, even for ambiguous history.
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        assert snapshot(session, (ContestResult, *HISTORY, ContestWeek)) == before


@pytest.mark.parametrize("mode", [LEGACY_PRACTICE_SCORING, PRECISE_PRACTICE_SCORING])
def test_conflicting_snapshot_mode_refuses_repair(pristine_database, mode):
    with pristine_database() as session:
        _, week, _, _, _ = seed(session)
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()
        week.practice_scoring_mode = mode
        if mode == PRECISE_PRACTICE_SCORING:
            row = session.scalar(select(ContestResult).join(Contest).where(
                Contest.metric_type == "practice_minutes"))
            row.precise_score = None
        session.commit()
        before = snapshot(session, (ContestResult, *HISTORY, ContestWeek))
        with pytest.raises(HTTPException, match="snapshots conflict"):
            finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW, repair_finalized=True)
        assert snapshot(session, (ContestResult, *HISTORY, ContestWeek)) == before


def test_season_tools_guard_rejects_incomplete_precision_schema():
    from sqlalchemy import create_engine
    from app.team_continuity_repair import schema_guard, RepairError
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for ddl in ("CREATE TABLE alembic_version (version_num TEXT)",
                    "CREATE TABLE team_families (id INTEGER)", "CREATE TABLE teams (id INTEGER)",
                    "INSERT INTO alembic_version VALUES ('t0p1q2r3s4t5')"):
            connection.execute(text(ddl))
        with pytest.raises(RepairError, match="team_family_constraints_missing"):
            schema_guard(connection)
    engine.dispose()


def test_precise_mode_survives_complete_result_loss(pristine_database):
    with pristine_database() as session:
        _, week, _, _, _ = seed(session, base=240)
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()
        results = session.scalars(select(ContestResult).join(Contest).where(
            Contest.metric_type == "practice_minutes")).all()
        expected = {(row.contest_id, row.division, row.subject_key): (row.rank, row.effective_score)
                    for row in results}
        for row in results:
            session.delete(row)
        session.commit()
        history = snapshot(session)
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW, repair_finalized=True)
        session.commit()
        restored = session.scalars(select(ContestResult).join(Contest).where(
            Contest.metric_type == "practice_minutes")).all()
        assert {(row.contest_id, row.division, row.subject_key): (row.rank, row.effective_score)
                for row in restored} == expected
        assert snapshot(session) == history


@pytest.mark.parametrize("damage", ["missing_cutoff", "unmarked_partial_finalization"])
def test_incomplete_finalization_provenance_refuses_before_writes(pristine_database, damage):
    with pristine_database() as session:
        _, week, _, _, _ = seed(session)
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()
        if damage == "missing_cutoff":
            week.finalized_at = None
            reason = "original finalization cutoff is missing"
        else:
            week.status = "pending"
            week.finalized_at = None
            week.practice_scoring_mode = None
            reason = "created without one"
        session.commit()
        before = snapshot(session, (ContestResult, *HISTORY, ContestWeek))
        with pytest.raises(HTTPException, match=reason):
            finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW, repair_finalized=True)
        assert snapshot(session, (ContestResult, *HISTORY, ContestWeek)) == before
