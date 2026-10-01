"""Private Team eligibility and coherent earning time across staged activation.

Both backends use production services. SQL hooks advance only the clock; they
do not replace admission, locks, award creation, finalization, or activation.
"""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select

from app import contests, director_dashboard, models as m, practice_charts, team_authority
from app.contest_jobs import audit_or_repair_history
from tests.test_persistent_team_boundary_runtime import (
    BOUNDARY, activate, board, boundary_db, legacy_closing_roster, stage,
)
from tests.test_persistent_team_round2 import submit_boundary_board_activity


SUNDAY = date(2026, 10, 4)
MONDAY = date(2026, 10, 5)
SUNDAY_AT = BOUNDARY - timedelta(microseconds=1)


def approved_book(db, profile_id, key, *, private=False, team=True, minutes=30):
    """Create and approve through the actual service, including private review."""
    with db.factory() as session:
        verifier = m.TrustedVerifier(
            email=f"{key}@example.test", display_name="Verifier", pin_hash="test")
        session.add(verifier)
        session.flush()
        session.add(m.StudentVerifierConnection(
            profile_id=profile_id, verifier_id=verifier.id,
            status="accepted", role="verifier"))
        session.commit()
        created = practice_charts.create_practice_chart_verification_request(
            session, profile=session.get(m.WoodchuckProfile, profile_id),
            verifier_id=verifier.id,
            practice_date=db.clock["at"].astimezone(contests.CENTRAL).date(),
            minutes=minutes, submission_key=key,
            include_contests=not private, include_team_contests=team and not private,
        )
        review = practice_charts.respond_to_practice_chart_verification(
            session, verifier=verifier,
            verification_id=created.verification.id, decision="approved")
        assert review.status == "approved"
        return created.chart.id


@pytest.mark.parametrize("evidence", [
    "absent", "private", "private_pending", "individual_only", "team",
])
def test_team_rewards_require_team_qualifying_practice(boundary_db, evidence):
    db = boundary_db
    approved = stage(db)
    db.clock["at"] = BOUNDARY
    if evidence == "private_pending":
        approved_book(db, 2, "pending-private", private=True)
    assert activate(db, approved)["transaction_state"] == "committed"
    db.clock["at"] = BOUNDARY + timedelta(hours=13)
    approved_book(db, db.profile.id, "public-earner")
    if evidence not in {"absent", "private_pending"}:
        approved_book(db, 2, "other-earner", private=evidence == "private",
                      team=evidence != "individual_only", minutes=10)
    for activity in ("care", "hours", "marching", "trivia"):
        response = board(db, activity)
        assert response.status_code == 200, response.text
    with db.factory() as session:
        week = session.get(m.ContestWeek, 3)
        scores = contests._weekly_team_scores(session, week)["open"]
        assert scores["totals"] == {10: 40 if evidence == "team" else 30}
        if evidence != "team":
            assert scores["tpr"] == {10: 9.2}
        assert contests._weekly_team_activity_point_scores(session, week) == {10: 4}
        assert len(contests._eligible_weekly_team_rosters(session, week)[10]) == 20
        assert contests._student_emblem_keys_for_week(
            session, contest_week=week, profile_ids={db.profile.id}
        ) == {db.profile.id: "letter:A"}
        db.clock["at"] = BOUNDARY + timedelta(days=7, hours=13)
        contests.finalize_contest_week(session, week_start=MONDAY, now=db.clock["at"])
        session.commit()
        results = session.scalars(select(m.ContestResult).where(
            m.ContestResult.contest_week_id == week.id,
            m.ContestResult.subject_type == "team")).all()
        assert len(results) == 6 and {r.team_id for r in results} == {10}
        if evidence != "team":
            # Absent, private (including pending-boundary approval), and
            # individual-only practice produce exactly the same Team results.
            keys = dict(session.execute(select(m.Contest.id, m.Contest.key)).all())
            assert {(keys[r.contest_id], r.division, r.score, r.rank,
                     r.active_member_count) for r in results} == {
                ("team-lifetime-practice", "open", 30, 1, 0),
                ("team-weekly-activity-points", "open", 4, 1, 0),
                ("team-weekly-average-practice", "open", 30, 1, 1),
                ("team-weekly-average-practice", "verified", 30, 1, 1),
                ("team-weekly-practice", "open", 30, 1, 1),
                ("team-weekly-practice", "verified", 30, 1, 1),
            }
        grants = session.scalars(select(m.RewardGrant).where(
            m.RewardGrant.contest_result_id.in_([r.id for r in results]))).all()
        expected = {db.profile.id, 2} if evidence == "team" else {db.profile.id}
        assert {g.profile_id for g in grants} == expected
        assert len(grants) == 12 * len(expected)
        other_placements = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.profile_id == 2,
            m.CampPointAward.activity_type == "contest-placement",
            m.CampPointAward.duplicate_key.like("%:team:%"))).all()
        assert len(other_placements) == (6 if evidence == "team" else 0)
        wins = session.scalar(select(m.CrownProgress.qualifying_wins).where(
            m.CrownProgress.profile_id == 2, m.CrownProgress.category_key == "team-crown"))
        assert (wins or 0) == (6 if evidence == "team" else 0)
        assert not session.scalar(select(m.CrownAward.id).where(
            m.CrownAward.profile_id == 2, m.CrownAward.category_key == "team-crown"))
        assert session.scalar(select(func.count()).select_from(m.TeamWeekMembershipSnapshot).where(
            m.TeamWeekMembershipSnapshot.contest_week_id == week.id)) == 20
        if evidence in {"absent", "private", "private_pending"}:
            assert not session.scalar(select(m.RewardGrant.id).where(
                m.RewardGrant.profile_id == 2, m.RewardGrant.source_key.like("contest:%")))
            assert not session.scalar(select(m.CampPointAward.id).where(
                m.CampPointAward.profile_id == 2,
                m.CampPointAward.activity_type == "contest-placement"))


@pytest.mark.parametrize("operation", [
    "finalize", "legacy_finalize", "repair", "director",
])
def test_second_monday_operation_cannot_reuse_sunday_admission(boundary_db, operation):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = SUNDAY_AT
    approved_book(db, db.profile.id, "closing-public")
    if operation == "director":
        # Staging requires director contests to be finalized. An idempotent
        # service call must still validate its new time before the early return.
        with db.factory() as session:
            session.add(m.DirectorTeamContest(
                id=1, season_id=2, owner_profile_id=db.profile.id,
                title="Closing director contest", metric="total_minutes",
                starts_at=BOUNDARY - timedelta(days=7), ends_at=SUNDAY_AT,
                finalizes_at=SUNDAY_AT, status="finalized", finalized_at=SUNDAY_AT,
                created_at=SUNDAY_AT, updated_at=SUNDAY_AT))
            session.commit()
    approved = stage(db)

    def call(session):
        now = db.clock["at"]
        if operation == "director":
            return director_dashboard.finalize_director_contest(
                session, contest=session.get(m.DirectorTeamContest, 1),
                profile=session.get(m.WoodchuckProfile, db.profile.id), now=now)
        if operation == "repair":
            return audit_or_repair_history(session, week_start=date(2026, 9, 28),
                                           now=now, apply=True)
        finalizer = (contests._legacy_finalize_contest_week if operation == "legacy_finalize"
                     else contests.finalize_contest_week)
        return finalizer(session, week_start=date(2026, 9, 28), now=now)

    with db.factory() as session:
        award, created = contests.create_camp_point_award(
            session, profile=session.get(m.WoodchuckProfile, db.profile.id),
            activity_type="care", activity_date=SUNDAY, now=SUNDAY_AT)
        assert created
        session.flush()
        db.clock["at"] = BOUNDARY + timedelta(hours=14)
        with pytest.raises(HTTPException) as error:
            call(session)
        assert error.value.status_code == 503
        # Even a caller that catches rejection and commits cannot persist Monday rewards.
        session.commit()
        assert team_authority.utc(award.occurred_at) == SUNDAY_AT
    with db.factory() as session:
        with pytest.raises(HTTPException) as error:
            call(session)
        assert error.value.status_code == 503  # Fresh Monday admission is also blocked.
        session.rollback()
        assert session.get(m.ContestWeek, 2).status == "open"
        assert not session.scalar(select(m.CampPointAward.id).where(
            m.CampPointAward.occurred_at >= BOUNDARY))
        assert not session.scalar(select(m.RewardGrant.id).where(
            m.RewardGrant.source_key.like("contest:%")))
    assert activate(db, approved)["transaction_state"] == "committed"
    with db.factory() as session:
        week = contests.finalize_contest_week(
            session, week_start=date(2026, 9, 28), now=db.clock["at"])
        session.commit()
        assert week.status == "finalized"
        snapshots = session.scalars(select(m.TeamWeekMembershipSnapshot).where(
            m.TeamWeekMembershipSnapshot.contest_week_id == week.id)).all()
        assert len(snapshots) == 20 and {s.team_id for s in snapshots} == {10}
        grants = session.scalars(select(m.RewardGrant).where(
            m.RewardGrant.profile_id == db.profile.id,
            m.RewardGrant.source_key.like("contest:%"))).all()
        assert grants and {team_authority.utc(g.created_at) for g in grants} == {db.clock["at"]}
        awards = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.activity_type == "contest-placement")).all()
        assert awards and {a.team_id for a in awards} == {10}
        assert {team_authority.utc(a.occurred_at) for a in awards} == {db.clock["at"]}
        assert {team_authority.utc(a.created_at) for a in awards} == {db.clock["at"]}
        results = session.scalars(select(m.ContestResult).where(
            m.ContestResult.contest_week_id == week.id)).all()
        assert results and {team_authority.utc(r.created_at) for r in results} == {db.clock["at"]}
        if operation == "director":
            result, changed = call(session)
            assert not changed and team_authority.utc(result.finalized_at) == SUNDAY_AT


@pytest.mark.parametrize("pre_activation", [False, True])
def test_applied_repair_admits_current_operation_but_preserves_historical_earnings(
        boundary_db, pre_activation):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = SUNDAY_AT - timedelta(days=14) if pre_activation else SUNDAY_AT
    week_start = date(2026, 9, 14) if pre_activation else date(2026, 9, 28)
    if pre_activation:
        with db.factory() as session:
            end, deadline, final_after = contests.contest_week_schedule(week_start)
            session.add(m.ContestWeek(id=4, season_id=2, week_start=week_start,
                week_end=end, status="open", verification_deadline_at=deadline,
                finalize_after=final_after))
            session.commit()
    approved_book(db, db.profile.id, "repair-public")
    db.clock["at"] = SUNDAY_AT
    if pre_activation:
        with db.factory() as session:
            contests.finalize_contest_week(session, week_start=week_start, now=db.clock["at"])
            session.commit()
    approved = stage(db)
    db.clock["at"] = BOUNDARY + timedelta(hours=14)
    activate(db, approved)
    with db.factory() as session:
        week = contests.finalize_contest_week(session, week_start=week_start, now=db.clock["at"])
        session.commit()
        original_at = team_authority.utc(week.finalized_at)
        assert (original_at < BOUNDARY) is pre_activation
        grant = session.scalars(select(m.RewardGrant).where(
            m.RewardGrant.reward_type == "dandelion",
            m.RewardGrant.contest_result_id.is_not(None))).first()
        source = grant.source_key
        award = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.duplicate_key == f"{source}:camp-points")).one()
        session.delete(grant)
        session.delete(award)
        session.commit()
        stored = [(r.id, r.created_at, r.score, r.team_id) for r in
                  session.scalars(select(m.ContestResult).order_by(m.ContestResult.id))]
        surviving_grants = [(r.id, r.source_key, r.created_at) for r in
                            session.scalars(select(m.RewardGrant).order_by(m.RewardGrant.id))]
        db.clock["at"] += timedelta(days=8)
        report = audit_or_repair_history(session, week_start=week.week_start,
                                        now=db.clock["at"], apply=True)
        assert report["action"] == "repaired"
        session.commit()
        assert [(r.id, r.created_at, r.score, r.team_id) for r in
                session.scalars(select(m.ContestResult).order_by(m.ContestResult.id))] == stored
        for rid, key, at in surviving_grants:
            existing = session.get(m.RewardGrant, rid)
            assert (existing.source_key, existing.created_at) == (key, at)
        repaired = session.scalars(select(m.RewardGrant).where(
            m.RewardGrant.source_key == source, m.RewardGrant.reward_type == "dandelion")).one()
        repaired_award = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.duplicate_key == f"{source}:camp-points")).one()
        assert team_authority.utc(repaired.created_at) == original_at
        assert team_authority.utc(repaired_award.created_at) == original_at
        assert team_authority.utc(repaired_award.occurred_at) == original_at
        assert team_authority.utc(week.finalized_at) == original_at


def test_admitted_sunday_finalizer_can_finish_monday_with_sunday_earnings(boundary_db):
    db = boundary_db
    legacy_closing_roster(db)
    week_start = date(2026, 9, 14)
    with db.factory() as session:
        end, deadline, final_after = contests.contest_week_schedule(week_start)
        session.add(m.ContestWeek(id=4, season_id=2, week_start=week_start,
            week_end=end, status="open", verification_deadline_at=deadline,
            finalize_after=final_after))
        session.add(m.CrownProgress(profile_id=db.profile.id,
            category_key="team-crown", qualifying_wins=9))
        session.commit()
    db.clock["at"] = SUNDAY_AT - timedelta(days=14)
    approved_book(db, db.profile.id, "earlier-week")
    db.clock["at"] = SUNDAY_AT
    approved = stage(db)
    with db.factory() as session:
        at = team_authority.authority_write_time(session)
        assert at == SUNDAY_AT
        db.clock["at"] = BOUNDARY
        week = contests.finalize_contest_week(session, week_start=week_start, now=at)
        session.commit()
        assert team_authority.utc(week.finalized_at) == SUNDAY_AT
        results = session.scalars(select(m.ContestResult).where(
            m.ContestResult.contest_week_id == 4)).all()
        grants = session.scalars(select(m.RewardGrant).where(
            m.RewardGrant.source_key.like("contest:4:%"))).all()
        awards = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.duplicate_key.like("contest:4:%"))).all()
        crowns = session.scalars(select(m.CrownAward).where(
            m.CrownAward.profile_id == db.profile.id,
            m.CrownAward.category_key == "team-crown")).all()
        assert results and grants and awards and len(crowns) == 1
        assert {team_authority.utc(row.created_at) for row in
                [*results, *grants, *awards, *crowns]} == {SUNDAY_AT}
        assert {team_authority.utc(row.occurred_at) for row in awards} == {SUNDAY_AT}
        assert team_authority.utc(crowns[0].earned_at) == SUNDAY_AT
    assert activate(db, approved)["transaction_state"] == "committed"


@pytest.mark.parametrize("activity,crown_due", [
    ("care", False), ("trivia", False), ("incorrect_trivia", False), ("bonus", False),
    ("care", True), ("trivia", True),
])
def test_board_earning_and_rewards_keep_sunday_time_when_sql_runs_monday(
        boundary_db, activity, crown_due):
    db = boundary_db
    legacy_closing_roster(db)
    db.clock["at"] = SUNDAY_AT
    if crown_due:
        with db.factory() as session:
            for days in range(1, 10):
                at = SUNDAY_AT - timedelta(days=days)
                session.add(m.CampPointAward(
                    profile_id=db.profile.id, activity_type=activity, points_awarded=1,
                    occurred_at=at, created_at=at, team_id=10,
                    duplicate_key=contests._board_activity_key(SUNDAY - timedelta(days=days), activity)))
            session.commit()
    approved = stage(db)
    crossed = []
    with db.factory() as session:
        prior_grants = set(session.scalars(select(m.RewardGrant.id)))

    def cross_before_insert(connection, cursor, statement, parameters, context, many):
        if statement.startswith(("INSERT INTO camp_point_awards", "INSERT INTO daily_trivia_attempts")):
            crossed.append(statement.split(" (")[0])
            db.clock["at"] = BOUNDARY

    event.listen(db.engine, "before_cursor_execute", cross_before_insert)
    try:
        response = submit_boundary_board_activity(db, activity)
    finally:
        event.remove(db.engine, "before_cursor_execute", cross_before_insert)
    assert response.status_code == 200, response.text
    assert crossed and db.clock["at"] == BOUNDARY
    with db.factory() as session:
        awards = session.scalars(select(m.CampPointAward).where(
            m.CampPointAward.profile_id == db.profile.id,
            m.CampPointAward.occurred_at >= SUNDAY_AT)).all()
        grants = session.scalars(select(m.RewardGrant).where(
            m.RewardGrant.profile_id == db.profile.id,
            m.RewardGrant.id.not_in(prior_grants))).all()
        attempts = session.scalars(select(m.DailyTriviaAttempt).where(
            m.DailyTriviaAttempt.profile_id == db.profile.id)).all()
        if activity == "incorrect_trivia":
            assert not response.json()["correct"]
            assert awards == [] and grants == []
        else:
            assert len(awards) == len(grants) == 1
            assert awards[0].team_id == 10
            assert team_authority.utc(awards[0].occurred_at) == SUNDAY_AT
            assert team_authority.utc(awards[0].created_at) == SUNDAY_AT
            assert team_authority.utc(grants[0].created_at) == SUNDAY_AT
        assert len(attempts) == (1 if "trivia" in activity else 0)
        for attempt in attempts:
            assert attempt.activity_date == SUNDAY
            assert team_authority.utc(attempt.created_at) == SUNDAY_AT
        crowns = session.scalars(select(m.CrownAward).where(
            m.CrownAward.profile_id == db.profile.id)).all()
        assert len(crowns) == (1 if crown_due else 0)
        for crown in crowns:
            assert team_authority.utc(crown.earned_at) == SUNDAY_AT
            assert team_authority.utc(crown.created_at) == SUNDAY_AT
    assert activate(db, approved)["transaction_state"] == "committed"
