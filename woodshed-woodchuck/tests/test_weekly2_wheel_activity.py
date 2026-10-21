"""Receipt-qualified Activity and combined rule provenance on migrated databases.

Wheel records below are persisted synthetic producer-shaped evidence. No Wheel
funding, rotation, payout service, feature activation or concurrency is simulated.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from fastapi import HTTPException
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import contests
from app.age_privacy import declare_age
from app.contest_jobs import audit_or_repair_history
from app.economy import qualified_camp_point_clause
from app.models import (CampPointAward, Contest, ContestResult, PersistentTeamControl,
                        PracticeChart, PracticeChartVerification, RewardGrant,
                        TeamMembership, TeamWeekMembershipSnapshot, WoodchuckProfile)
from app.team_authority import LEGACY_RULES, PERSISTENT_RULES
from app.teams import create_and_join_team
from app.weekly_models import WeeklyWheelPlay
from tests.test_weekly2_foundation_migration import db, config, revision

NOW = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
FINAL = datetime(2026, 10, 12, 18, tzinfo=timezone.utc)
ACTIVITY = ("weekly-camp-points", "team-weekly-activity-points")


def snapshot(session):
    return deepcopy({table.name: list(session.execute(
        select(*table.columns).order_by(*table.primary_key.columns)))
        for table in ContestResult.metadata.sorted_tables})


@pytest.fixture
def session(db):
    command.upgrade(config(), "w25weekly004")
    assert revision(db) == "w25weekly004"
    with Session(db, expire_on_commit=False) as value:
        yield value


def candidate(session, persistent=False):
    season, definitions, week = contests.ensure_current_contest_data(session, now=NOW)
    people, teams = [], []
    for number in range(3):
        person = WoodchuckProfile(woodchuck_id=f"WHEEL-RULE-{number}",
            display_name=f"Synthetic {number}", pin_hash="unused", instrument="Flute",
            level="Beginner", goal="Practice")
        session.add(person)
        session.flush()
        declare_age(session, person.id, "adult", at=NOW-timedelta(days=90))
        session.commit()
        team, _ = create_and_join_team(session, profile=person, season=season,
            name=f"Wheel Rules {number}", emblem_key=f"letter:{chr(65+number)}", now=NOW)
        session.commit()
        people.append(person)
        teams.append(team)
    if persistent:
        # Reviewed persistent-Team fixture shape: explicitly seeded operating
        # authority, not an operational cutover or invented student entitlement.
        boundary = datetime(2026, 10, 5, 5, tzinfo=timezone.utc)
        control = session.get(PersistentTeamControl, 1)
        if control is None:
            control = PersistentTeamControl(id=1)
            session.add(control)
        control.activated_at, control.rules_from_week_start = boundary, boundary.date()
        for team in teams:
            team.is_operating = True
        for membership in session.scalars(select(TeamMembership)):
            membership.is_persistent = True
        week.team_membership_rules_version = PERSISTENT_RULES
    for person, team, points in zip(people[:2], teams[:2], (12, 8)):
        session.add(CampPointAward(profile_id=person.id, team_id=team.id,
            activity_type="trivia", points_awarded=points, duplicate_key=f"fixture:{person.id}",
            occurred_at=NOW, created_at=NOW))
    session.commit()
    return SimpleNamespace(session=session, season=season, week=week, people=people,
        teams=teams, contests={row.key: row for row in definitions}, persistent=persistent)


def wheel_evidence(c, *, person=None, team=None, outcomes=None, at=NOW, slot=1):
    """Persist the accepted completion/receipt shape, never call a prize writer."""
    s = c.session
    person, team = person or c.people[2], team or c.teams[2]
    play = WeeklyWheelPlay(id=uuid4().hex, profile_id=person.id, request_key=uuid4().hex,
        funded_day=at.astimezone(contests.CENTRAL).date(), daily_ordinal=slot,
        funded_at=at-timedelta(seconds=1), completed_at=at,
        outcomes=outcomes if outcomes is not None else ["activity"])
    source = f"weekly-wheel:{play.id}:{len(play.outcomes)}"
    award = CampPointAward(profile_id=person.id, team_id=team.id,
        activity_type="wheel-prize", points_awarded=14, duplicate_key=source,
        occurred_at=at, created_at=at)
    grant = RewardGrant(profile_id=person.id, source_key=source,
        reward_type="activity", amount=14, created_at=at)
    s.add_all([play, award, grant])
    s.commit()
    return play, award, grant


@pytest.mark.parametrize("persistent", [False, True])
def test_receipted_wheel_live_scores_and_readonly(session, persistent, record_property):
    c = candidate(session, persistent)
    play, award, grant = wheel_evidence(c, outcomes=["spin_again", "activity"])
    before = snapshot(session)
    accepted = session.scalars(select(CampPointAward.id).where(qualified_camp_point_clause())).all()
    record_property("wheel_fixture", {"play": play.id, "award": award.id,
        "receipt": grant.id, "accepted": award.id in accepted, "synthetic": True})
    assert award.id in accepted, "Genuine saved Wheel Activity evidence is excluded"
    individual = contests.weekly_camp_points(session, contest_week=c.week,
        current_profile_id=c.people[2].id)["open"]
    assert [(r["display_name"], r["total_points"]) for r in individual] == [
        (c.people[i].display_name, points) for i, points in ((2, 14), (0, 12), (1, 8))]
    teams = contests.team_leaderboards(session, season=c.season, contest_week=c.week,
        source_cutoff=FINAL)[ACTIVITY[1]]["open"]
    assert [(r["team_id"], r["score"]) for r in teams] == [
        (c.teams[i].id, points) for i, points in ((2, 14), (0, 12), (1, 8))]
    assert contests.student_camp_point_totals(session, profile_id=c.people[2].id,
        now=NOW, season=c.season, contest_week=c.week)["camp_points_this_week"] == 14
    session.commit()
    assert snapshot(session) == before


def test_forged_and_mismatched_wheel_evidence_is_excluded(session, record_property):
    c = candidate(session)
    play, award, grant = wheel_evidence(c)
    baseline = snapshot(session)
    variants = [
        (award, "points_awarded", 13), (award, "activity_type", "unknown-prize"),
        (award, "profile_id", c.people[0].id),
        (award, "created_at", NOW+timedelta(seconds=1)),
        (grant, "amount", 15), (grant, "reward_type", "xp"),
        (grant, "profile_id", c.people[0].id), (grant, "category_key", "forged"),
        (grant, "created_at", NOW+timedelta(seconds=1)),
        (play, "profile_id", c.people[0].id), (play, "completed_at", None),
        (play, "completed_at", NOW+timedelta(seconds=1)),
        (play, "funded_at", NOW+timedelta(seconds=1)),
        *[(play, "outcomes", value) for value in ([], {}, None, "activity", ["xp"], ["spin_again"])],
    ]
    rejected = []
    for obj, key, value in variants:
        with session.begin_nested() as tx:
            setattr(obj, key, value)
            session.flush()
            before = snapshot(session)
            assert award.id not in session.scalars(select(CampPointAward.id).where(
                qualified_camp_point_clause())).all(), (key, value)
            assert snapshot(session) == before
            rejected.append((type(obj).__name__, key, value))
            tx.rollback()
    for source in ("weekly-wheel:forged:1", f"weekly-wheel:{play.id}:2", f"other:{play.id}:1"):
        with session.begin_nested() as tx:
            award.duplicate_key = grant.source_key = source
            session.flush()
            assert award.id not in session.scalars(select(CampPointAward.id).where(
                qualified_camp_point_clause())).all()
            rejected.append(("source", source))
            tx.rollback()
    for missing in (grant, play):
        with session.begin_nested() as tx:
            session.delete(missing)
            session.flush()
            assert award.id not in session.scalars(select(CampPointAward.id).where(
                qualified_camp_point_clause())).all()
            rejected.append(("missing", type(missing).__name__))
            tx.rollback()
    assert snapshot(session) == baseline
    record_property("rejected_variants", rejected)


def add_book(c):
    for minutes, approved in ((30, False), (20, True)):
        chart = PracticeChart(profile_id=c.people[2].id, team_id=c.teams[2].id,
            practice_date=NOW.date(), minutes=minutes, instrument="Flute", source="p-book",
            created_at=NOW, include_contests=True, include_team_contests=True, credits_awarded=0)
        c.session.add(chart)
        c.session.flush()
        if approved:
            c.session.add(PracticeChartVerification(practice_chart_id=chart.id,
                status="approved", responded_at=NOW+timedelta(minutes=1)))
    c.session.commit()


def activity_results(c):
    return [(definition.key, row.subject_key, row.score, row.rank, row.medal)
            for row, definition in c.session.execute(select(ContestResult, Contest).join(Contest).where(
                ContestResult.contest_week_id == c.week.id, Contest.key.in_(ACTIVITY)
            ).order_by(Contest.key, ContestResult.rank))]


@pytest.mark.parametrize("persistent", [False, True])
def test_combined_finalization_and_compatible_missing_result_repair(session, persistent, record_property):
    c = candidate(session, persistent)
    wheel_evidence(c)
    add_book(c)
    before = snapshot(session)
    contests.finalize_contest_week(session, week_start=c.week.week_start, now=FINAL)
    session.commit()
    expected = "contest_finalizer_persistent_v3" if persistent else "contest_finalizer_v3"
    assert c.week.finalizer_rules_version == expected
    for key, subjects in ((ACTIVITY[0], c.people), (ACTIVITY[1], c.teams)):
        assert [r[1:] for r in activity_results(c) if r[0] == key] == [
            (str(subjects[i].id), points, rank, medal)
            for i, points, rank, medal in ((2, 14, 1, "gold"), (0, 12, 2, "silver"), (1, 8, 3, "bronze"))]
    practice = {r.division: r for r in session.scalars(select(ContestResult).join(Contest).where(
        ContestResult.contest_week_id == c.week.id, Contest.key == "weekly-points-leaders"))}
    assert practice["open"].precise_score == 50 and practice["verified"].precise_score == 20
    assert snapshot(session)["practice_charts"] == before["practice_charts"]
    assert snapshot(session)["practice_chart_verifications"] == before["practice_chart_verifications"]
    saved = snapshot(session)
    contests.finalize_contest_week(session, week_start=c.week.week_start, now=FINAL+timedelta(days=1))
    session.commit()
    assert snapshot(session) == saved
    missing = session.scalar(select(ContestResult).where(
        ContestResult.contest_week_id == c.week.id,
        ContestResult.contest_id == c.contests[ACTIVITY[0]].id,
        ContestResult.profile_id == c.people[2].id))
    wanted = (missing.score, missing.rank, missing.medal)
    session.delete(missing)
    session.commit()
    gap = snapshot(session)
    preview = audit_or_repair_history(session, week_start=c.week.week_start, now=FINAL+timedelta(days=1))
    assert preview["action"] == "repaired" and preview["created"]["results"] == 1
    assert snapshot(session) == gap
    applied = audit_or_repair_history(session, week_start=c.week.week_start,
        now=FINAL+timedelta(days=1), apply=True)
    session.commit()
    assert applied["created"]["results"] == 1
    after = snapshot(session)
    assert all(after[k] == v for k, v in gap.items() if k != "contest_results")
    assert all(row in after["contest_results"] for row in gap["contest_results"])
    restored = session.scalar(select(ContestResult).where(
        ContestResult.contest_week_id == c.week.id,
        ContestResult.contest_id == c.contests[ACTIVITY[0]].id,
        ContestResult.profile_id == c.people[2].id))
    assert (restored.score, restored.rank, restored.medal) == wanted
    for apply in (False, True):
        report = audit_or_repair_history(session, week_start=c.week.week_start,
            now=FINAL+timedelta(days=2), apply=apply)
        assert report["action"] == "unchanged" and not any(report["created"].values())
    contests.finalize_contest_week(session, week_start=c.week.week_start,
        now=FINAL+timedelta(days=2), repair_finalized=True)
    session.commit()
    assert snapshot(session) == after
    record_property("combined_rules", {"version": expected, "activity": activity_results(c),
        "open_minutes": 50, "verified_minutes": 20, "restored_activity": wanted})


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("version", ["v1", "v2", "unknown", "other", "wrong-membership-v3"])
def test_old_or_unknown_history_refuses_all_repair_paths_without_writes(session, persistent, version):
    c = candidate(session, persistent)
    wheel_evidence(c)
    c.week.status, c.week.finalized_at = "finalized", FINAL
    c.week.practice_scoring_mode = "precise_seconds"
    prefix = "contest_finalizer_persistent_" if persistent else "contest_finalizer_"
    c.week.finalizer_rules_version = (None if version == "unknown" else "future-rules" if version == "other"
        else ("contest_finalizer_v3" if persistent else "contest_finalizer_persistent_v3")
        if version == "wrong-membership-v3" else prefix+version)
    for key, subjects in ((ACTIVITY[0], c.people), (ACTIVITY[1], c.teams)):
        for rank, (subject, score) in enumerate(zip(subjects[:2], (12, 8)), 1):
            is_team = key == ACTIVITY[1]
            session.add(ContestResult(contest_week_id=c.week.id, contest_id=c.contests[key].id,
                division="open", subject_type="team" if is_team else "student", subject_key=str(subject.id),
                team_id=subject.id if is_team else None, profile_id=None if is_team else subject.id,
                display_name_snapshot=subject.display_name, score=score, rank=rank,
                medal="gold" if rank == 1 else "silver", created_at=FINAL))
    session.commit()
    before = snapshot(session)
    statements = []
    def record(_c, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            statements.append(statement)
    event.listen(session.get_bind(), "before_cursor_execute", record)
    try:
        contests.finalize_contest_week(session, week_start=c.week.week_start, now=FINAL+timedelta(days=1))
        for apply in (False, True):
            report = audit_or_repair_history(session, week_start=c.week.week_start,
                now=FINAL+timedelta(days=1), apply=apply)
            assert report["action"] == "manual_review"
            assert report["reason"] == ("historical_rules_unknown" if version == "unknown"
                                         else "historical_rules_incompatible")
            assert report["before"] == report["after"] and not any(report["created"].values())
        with pytest.raises(HTTPException) as error:
            contests.finalize_contest_week(session, week_start=c.week.week_start,
                now=FINAL+timedelta(days=1), repair_finalized=True)
        assert error.value.status_code == 409
        session.commit()
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", record)
    assert statements == []
    assert snapshot(session) == before


def test_other_activity_and_board_receipts_unchanged(session):
    c = candidate(session)
    expected = []
    for activity, amount, source, qualified in (
        ("care", 1, "board-self-report-v2:2026-10-06:care", True),
        ("hours", 1, "board-self-report-v2:2026-10-06:hours", True),
        ("marching", 1, "board-self-report-v2:2026-10-06:marching", True),
        ("quest", 2, "bonus-challenge:self-report-v2:2026-10-06", True),
        ("care", 99, "legacy:99", False), ("care", 7, "legacy:7", False),
        ("care", 88, "legacy:88", False), ("unknown", 14, "unrelated:1", False),
    ):
        row = CampPointAward(profile_id=c.people[2].id, team_id=c.teams[2].id,
            activity_type=activity, points_awarded=amount, duplicate_key=source,
            occurred_at=NOW, created_at=NOW)
        session.add(row)
        session.flush()
        expected.append((row.id, qualified))
    session.add(RewardGrant(profile_id=c.people[2].id,
        source_key="bonus-challenge:2026-10-06:synthetic", category_key="board-bonus-v1:Flute",
        reward_type="dandelion", amount=5, created_at=NOW))
    session.commit()
    before = snapshot(session)
    ids = set(session.scalars(select(CampPointAward.id).where(qualified_camp_point_clause())))
    assert all((ident in ids) == qualified for ident, qualified in expected)
    assert snapshot(session) == before


@pytest.mark.parametrize("persistent", [False, True])
def test_wheel_cutoffs_and_frozen_team_attribution(session, persistent):
    from app.teams import select_team
    c = candidate(session, persistent)
    _, award, _ = wheel_evidence(c)
    wheel_evidence(c, at=NOW-timedelta(days=7), slot=2)
    wheel_evidence(c, at=FINAL+timedelta(seconds=1), slot=3)
    assert contests._weekly_team_activity_point_scores(session, c.week,
        source_cutoff=NOW-timedelta(microseconds=1)) == {}
    assert contests._weekly_team_activity_point_scores(session, c.week,
        source_cutoff=NOW)[c.teams[2].id] == 14
    contests.finalize_contest_week(session, week_start=c.week.week_start, now=FINAL)
    session.commit()
    frozen = list(session.execute(select(TeamWeekMembershipSnapshot.__table__)).mappings())
    assert {r["profile_id"]: r["team_id"] for r in frozen}[c.people[2].id] == c.teams[2].id
    select_team(session, profile=c.people[2], season=c.season, team=c.teams[0],
        now=FINAL+timedelta(days=1))
    session.commit()
    before = snapshot(session)
    assert award.team_id == c.teams[2].id
    assert list(session.execute(select(TeamWeekMembershipSnapshot.__table__)).mappings()) == frozen
    assert contests._eligible_weekly_team_rosters(session, c.week)[c.teams[2].id] == {c.people[2].id}
    contests.finalize_contest_week(session, week_start=c.week.week_start,
        now=FINAL+timedelta(days=2), repair_finalized=True)
    session.commit()
    assert snapshot(session) == before


@pytest.mark.parametrize("case", [
    "missing", "pending", "rejected", "approved-no-time", "approved", "deadline", "late",
    "approval-transition", "scope-cutoffs", "private-under13", "private-unknown", "attribution",
    "finalization", "reward-recipient",
])
def test_existing_book_contract_on_migrated_database(db, monkeypatch, case):
    """Execute original assertion bodies, replacing only create_all fixture setup."""
    from tests import test_open_practice_eligibility as original
    command.upgrade(config(), "w25weekly004")
    monkeypatch.setattr(original, "database", lambda: Session(db, expire_on_commit=False))
    fixture = original.competition.__wrapped__()
    context = next(fixture)
    try:
        variants = {"missing": (None, None, False), "pending": ("pending", None, False),
            "rejected": ("rejected", "on_time", False), "approved-no-time": ("approved", None, False),
            "approved": ("approved", "on_time", True), "deadline": ("approved", "deadline", True),
            "late": ("approved", "late", False)}
        if case in variants:
            original.test_open_reporting_does_not_require_verified_evidence(context, *variants[case])
        elif case == "approval-transition":
            original.test_approval_adds_verified_without_moving_or_double_counting_open(context)
        elif case == "scope-cutoffs":
            original.test_opt_out_team_attribution_source_and_week_boundaries(context)
        elif case.startswith("private-"):
            original.test_pending_private_charts_stay_private_and_deletion_preserves_team_attribution(
                context, case.split("-", 1)[1])
        elif case == "attribution":
            original.test_unapproved_chart_attribution_survives_later_membership_change(context)
        elif case == "finalization":
            original.test_future_finalization_matches_live_open_and_is_idempotent(context)
        else:
            original.test_open_team_reporting_keeps_approved_reward_recipient_evidence(context)
    finally:
        fixture.close()


def test_parent_authorized_child_wheel_privacy_and_withdrawal(db, monkeypatch):
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.main import app
    from app import tester_enrollments as testers, child_authorization as consent
    from app.age_privacy import eligible
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import (
        test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED)
    command.upgrade(config(), "w25weekly004")
    factory = sessionmaker(db, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(testers, "clock", lambda: CLAIMED)
    # Reviewed C002 helper uses genuine signup/parent authorization paths with
    # synthetic provider/email fixtures. No permission row is fabricated here.
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(
        factory, monkeypatch, "/prebeta/C002", "C002", "DIRECTOR1")
    with factory() as s:
        child = s.scalar(select(WoodchuckProfile))
        child_id, wid = child.id, child.woodchuck_id
        assert eligible(s, child_id)
        c = candidate(s)
        wheel_evidence(c, person=child)
        before = snapshot(s)
        own = contests.weekly_camp_points(s, contest_week=c.week, current_profile_id=child_id)["open"]
        assert any(row["display_name"] == child.display_name and row["total_points"] == 14 for row in own)
        public = contests.weekly_camp_points(s, contest_week=c.week,
            current_profile_id=c.people[0].id)["open"]
        assert all(row["display_name"] != child.display_name for row in public)
        team = contests.team_leaderboards(s, season=c.season, contest_week=c.week)[ACTIVITY[1]]["open"]
        assert c.teams[2].id not in {row["team_id"] for row in team}
        assert snapshot(s) == before
    client = TestClient(app)
    try:
        assert client.post("/account/login", data={"woodchuck_id": wid, "pin": "2468"}).status_code == 200
        assert client.get("/contests/current").status_code == 200
        with factory() as s:
            consent.withdraw_evidence(s, s.scalar(select(ConsentEvidence)))
            s.commit()
            assert not eligible(s, child_id)
            preserved = snapshot(s)
        assert client.get("/contests/current").status_code == 401
        with factory() as s:
            after = snapshot(s)
            # Existing session retirement may add a revoked nonce; business
            # history, rewards and account state are otherwise unchanged.
            assert all(after[k] == v for k, v in preserved.items() if k != "revoked_browser_sessions")
            assert all(row in after["revoked_browser_sessions"] for row in preserved["revoked_browser_sessions"])
    finally:
        client.close()
