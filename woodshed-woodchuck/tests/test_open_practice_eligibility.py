"""Open reports are independent of approval; evidence and history stay fenced."""
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import contests
from app.account_deletion import anonymize_woodchuck_account
from app.age_privacy import declare_age
from app.models import (
    Contest, ContestResult, ContestWeek, PracticeChart, PracticeChartVerification, RewardGrant,
)
from app.practice_duration import qualified_practice_clause, team_qualified_practice_clause
from app.team_authority import LEGACY_RULES, PERSISTENT_RULES
from app.teams import create_and_join_team, select_team
from app.xp import xp_sources
from tests.test_team_contests import NOW, FINAL_NOW, add_chart, add_profile, database
from tests.test_teams import profile as unscreened_profile


@pytest.fixture
def competition():
    with database() as session:
        season, _, week = contests.ensure_current_contest_data(session, now=NOW)
        person = add_profile(session, 101)
        session.commit()
        team, _ = create_and_join_team(
            session, profile=person, season=season, name="Open Reports",
            emblem_key="letter:O", now=NOW,
        )
        yield session, season, week, person, team


def standings(competition):
    session, season, week, person, _ = competition
    return (
        contests.weekly_student_points(session, contest_week=week, current_profile_id=person.id),
        contests.weekly_practice_by_instrument(session, contest_week=week),
        contests.team_leaderboards(session, season=season, contest_week=week),
    )


@pytest.mark.parametrize("status,timing,verified", [
    (None, None, False),
    ("pending", None, False),
    ("rejected", "on_time", False),
    ("approved", None, False),
    ("approved", "on_time", True),
    ("approved", "deadline", True),
    ("approved", "late", False),
])
def test_open_reporting_does_not_require_verified_evidence(competition, status, timing, verified):
    session, _, week, person, team = competition
    chart = add_chart(session, person, team.id, 30, approved=False, created_at=NOW)
    if status:
        response_at = {
            None: None, "on_time": NOW,
            "deadline": week.verification_deadline_at,
            "late": week.verification_deadline_at + timedelta(microseconds=1),
        }[timing]
        session.add(PracticeChartVerification(
            practice_chart_id=chart.id, status=status, responded_at=response_at,
        ))
    session.commit()

    students, instruments, teams = standings(competition)
    assert students["open"][0]["total_minutes"] == 30
    assert instruments == {"open": [{"rank": 1, "instrument": "Flute", "total_minutes": 30}]}
    for key in ("team-weekly-practice", "team-weekly-average-practice", "team-lifetime-practice"):
        assert teams[key]["open"][0]["score"] == 30
    assert teams["team-practice-rating"]["open"][0]["active_member_count"] == 1
    assert bool(students["verified"]) is verified
    assert bool(teams["team-weekly-practice"]["verified"]) is verified
    if verified:
        assert students["verified"][0]["total_minutes"] == 30
        assert teams["team-weekly-practice"]["verified"][0]["score"] == 30
    assert students["pristine"] == []
    # Reporting never turns pending/rejected/missing-response charts into XP
    # evidence. Deadline qualification remains a separate Verified constraint.
    qualified = status == "approved" and timing is not None
    assert xp_sources(session, profile_id=person.id)["practice_minutes"] == (30 if qualified else 0)
    assert session.scalars(select(PracticeChart.id).where(qualified_practice_clause())).all() == (
        [chart.id] if qualified else []
    )
    assert chart.credits_awarded == 0
    assert session.scalars(select(RewardGrant)).all() == []


def test_approval_adds_verified_without_moving_or_double_counting_open(competition):
    session, _, _, person, team = competition
    chart = add_chart(session, person, team.id, 40, approved=False, created_at=NOW)
    pending = PracticeChartVerification(practice_chart_id=chart.id, status="pending")
    session.add(pending)
    session.commit()
    before_students, before_instruments, before_teams = standings(competition)
    assert before_students["verified"] == []
    pending.status, pending.responded_at = "approved", NOW
    # Historical/imported multiple verification rows must not duplicate a chart.
    session.add(PracticeChartVerification(
        practice_chart_id=chart.id, status="approved", responded_at=NOW,
    ))
    session.commit()
    students, instruments, teams = standings(competition)
    assert students["open"] == before_students["open"]
    assert instruments == before_instruments
    for key in teams:
        assert teams[key]["open"] == before_teams[key]["open"]
    assert students["open"][0]["total_minutes"] == students["verified"][0]["total_minutes"] == 40
    assert teams["team-weekly-practice"]["open"][0]["score"] == 40
    assert teams["team-weekly-practice"]["verified"][0]["score"] == 40


def test_opt_out_team_attribution_source_and_week_boundaries(competition):
    session, _, week, person, team = competition
    # Both ends of the half-open practice-date interval are exercised.
    for day in (week.week_start, week.week_end - timedelta(days=1)):
        add_chart(session, person, team.id, 10, approved=False, practice_date=day, created_at=NOW)
    for day in (week.week_start - timedelta(days=1), week.week_end):
        add_chart(session, person, team.id, 100, approved=False, practice_date=day, created_at=NOW)
    for approved in (False, True):
        opted_out = add_chart(session, person, team.id, 200, approved=approved, created_at=NOW)
        opted_out.include_contests = False
        personal_only = add_chart(session, person, team.id, 5, approved=approved, created_at=NOW)
        personal_only.include_team_contests = False
        unattributed = add_chart(session, person, None, 5, approved=approved, created_at=NOW)
        pristine = add_chart(session, person, team.id, 300, approved=approved, created_at=NOW)
        pristine.source, pristine.detected_playing_seconds = "pristine", 18000
    session.commit()

    students, instruments, teams = standings(competition)
    assert students["open"][0]["total_minutes"] == 40
    assert students["verified"][0]["total_minutes"] == 10
    assert instruments["open"][0]["total_minutes"] == 40
    assert teams["team-weekly-practice"]["open"][0]["score"] == 20
    assert teams["team-weekly-practice"]["verified"] == []
    assert teams["team-lifetime-practice"]["open"][0]["score"] == 120
    assert students["pristine"] == []
    assert session.scalars(select(PracticeChart.id).where(team_qualified_practice_clause())).all() == [unattributed.id]


@pytest.mark.parametrize("private_age", ["under13", "unknown"])
def test_pending_private_charts_stay_private_and_deletion_preserves_team_attribution(competition, private_age):
    session, _, _, person, team = competition
    public_chart = add_chart(session, person, team.id, 30, approved=False, created_at=NOW)
    private = unscreened_profile(session, 102)
    declare_age(session, private.id, private_age, at=NOW - timedelta(days=30))
    private_chart = add_chart(session, private, team.id, 100, approved=False, created_at=NOW)
    session.commit()
    students, instruments, teams = standings(competition)
    assert [row["display_name"] for row in students["open"]] == [person.display_name]
    assert instruments["open"][0]["total_minutes"] == 30
    assert teams["team-weekly-practice"]["open"][0]["score"] == 30
    assert teams["team-lifetime-practice"]["open"][0]["score"] == 30
    anonymize_woodchuck_account(session, profile=person, now=NOW + timedelta(days=2))
    session.commit()
    students, _, teams = standings(competition)
    assert students["open"] == []
    assert teams["team-weekly-practice"]["open"][0]["score"] == 30
    assert public_chart.team_id == private_chart.team_id == team.id


def test_unapproved_chart_attribution_survives_later_membership_change(competition):
    session, season, _, person, original_team = competition
    owner = add_profile(session, 103)
    session.commit()
    new_team, _ = create_and_join_team(
        session, profile=owner, season=season, name="Later Team",
        emblem_key="letter:L", now=NOW,
    )
    chart = add_chart(session, person, original_team.id, 30, approved=False, created_at=NOW)
    session.commit()
    select_team(session, profile=person, season=season, team=new_team, now=NOW + timedelta(days=7))
    session.commit()
    session.refresh(chart)
    _, _, teams = standings(competition)
    assert chart.team_id == original_team.id
    for key in ("team-weekly-practice", "team-lifetime-practice"):
        assert [(row["team_id"], row["score"]) for row in teams[key]["open"]] == [(original_team.id, 30)]


def snapshot(session):
    # Compare every durable row, including rewards, identities, and rule metadata.
    return deepcopy({table.name: list(session.execute(select(*table.columns).order_by(*table.primary_key.columns)))
                     for table in ContestWeek.metadata.sorted_tables})


def test_future_finalization_matches_live_open_and_is_idempotent(competition):
    session, _, week, person, team = competition
    pending = add_chart(session, person, team.id, 30, approved=False, created_at=NOW)
    session.add(PracticeChartVerification(practice_chart_id=pending.id, status="pending"))
    approved = add_chart(session, person, team.id, 20, approved=True, created_at=NOW)
    session.add(PracticeChartVerification(practice_chart_id=approved.id, status="approved", responded_at=NOW))
    late = add_chart(session, person, team.id, 10, approved=False, created_at=NOW)
    session.add(PracticeChartVerification(
        practice_chart_id=late.id, status="approved",
        responded_at=week.verification_deadline_at + timedelta(seconds=1),
    ))
    excluded = add_chart(session, person, team.id, 100, approved=True, created_at=NOW)
    excluded.include_contests = False
    session.commit()
    students, instruments, teams = standings(competition)
    assert students["open"][0]["total_minutes"] == 60
    assert students["verified"][0]["total_minutes"] == 20
    # Stored future-created records cannot enter this finalization's cutoff.
    add_chart(session, person, team.id, 100, approved=False, created_at=FINAL_NOW + timedelta(seconds=1))
    session.commit()
    contests.finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    results = {(contest.key, row.division): row for row, contest in session.execute(
        select(ContestResult, Contest).join(Contest).where(ContestResult.contest_week_id == week.id)
    )}
    assert results["weekly-points-leaders", "open"].precise_score == 60
    assert results["weekly-points-leaders", "verified"].precise_score == 20
    assert results["weekly-practice-by-instrument", "open"].precise_score == instruments["open"][0]["total_minutes"]
    for key in ("team-weekly-practice", "team-weekly-average-practice", "team-lifetime-practice"):
        assert results[key, "open"].precise_score == teams[key]["open"][0]["score"]
    assert week.finalizer_rules_version == "contest_finalizer_v2"
    assert pending.credits_awarded == 0
    before = snapshot(session)
    for repair in (False, True):
        contests.finalize_contest_week(
            session, week_start=week.week_start, now=FINAL_NOW + timedelta(days=1), repair_finalized=repair,
        )
        session.commit()
        assert snapshot(session) == before


@pytest.mark.parametrize("membership_rules,old_finalizer", [
    (LEGACY_RULES, "contest_finalizer_v1"),
    (PERSISTENT_RULES, "contest_finalizer_persistent_v1"),
])
def test_pre_fix_finalized_history_cannot_be_recomputed(competition, membership_rules, old_finalizer):
    session, _, week, person, team = competition
    add_chart(session, person, team.id, 40, approved=False, created_at=NOW)
    week.status, week.finalized_at = "finalized", FINAL_NOW
    week.practice_scoring_mode = "precise_seconds"
    week.team_membership_rules_version = membership_rules
    week.finalizer_rules_version = old_finalizer
    contest = session.scalar(select(Contest).where(Contest.key == "weekly-points-leaders"))
    session.add(ContestResult(
        contest_week_id=week.id, contest_id=contest.id, division="open",
        subject_type="student", subject_key=str(person.id), profile_id=person.id,
        display_name_snapshot=person.display_name, score=29, precise_score=29,
        rank=1, medal="gold", created_at=FINAL_NOW,
    ))
    session.commit()
    before = snapshot(session)
    assert contests.finalizer_rules_version(week) == (
        "contest_finalizer_v2" if membership_rules == LEGACY_RULES else "contest_finalizer_persistent_v2"
    )
    contests.finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW + timedelta(days=1))
    session.commit()
    assert snapshot(session) == before
    with pytest.raises(HTTPException, match="historical_rules_incompatible") as error:
        contests.finalize_contest_week(
            session, week_start=week.week_start, now=FINAL_NOW + timedelta(days=1), repair_finalized=True,
        )
    assert error.value.status_code == 409
    assert snapshot(session) == before


def test_open_team_reporting_keeps_approved_reward_recipient_evidence(competition):
    session, _, week, person, team = competition
    chart = add_chart(session, person, team.id, 40, approved=False, created_at=NOW)
    session.add(PracticeChartVerification(practice_chart_id=chart.id, status="pending"))
    session.commit()
    contests.finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    assert session.scalar(select(ContestResult).join(Contest).where(
        Contest.key == "team-weekly-practice", ContestResult.division == "open",
    )).precise_score == 40
    assert session.scalars(select(RewardGrant).where(
        RewardGrant.profile_id == person.id, RewardGrant.source_key.like("%:team-%"),
    )).all() == []
    assert chart.credits_awarded == 0
    assert xp_sources(session, profile_id=person.id)["practice_minutes"] == 0
