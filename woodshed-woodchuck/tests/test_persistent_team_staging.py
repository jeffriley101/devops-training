"""One approved Monday boundary; staging cannot leak persistent attribution."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m, persistent_team_cutover as cutover
from tests.test_persistent_team_cutover import (
    BOUNDARY, BOUNDARY_AT, MEMBERSHIP_IDS, NOW, TEAM_IDS,
    activate, db, plan, snapshot, stage,
)


def test_stage_only_persists_approval_without_changing_authority_or_history(db):
    before = snapshot(db[1])
    approved = plan(db)
    receipt = stage(db, approved)
    after = snapshot(db[1])
    assert receipt["operation"] == "staged"
    assert after["persistent_team_control"][0]["activated_at"] is None
    assert after["persistent_team_control"][0]["staged_for"] == cutover.normalized(BOUNDARY_AT)
    for name in before:
        if name != "persistent_team_control":
            assert after[name] == before[name]
    with pytest.raises(cutover.CutoverError, match="activation_receipt"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)


def test_activation_cannot_promote_before_exact_boundary(db):
    approved = plan(db)
    stage(db, approved)
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="boundary_not_reached"):
        activate(db, approved, now=BOUNDARY_AT-timedelta(microseconds=1))
    assert snapshot(db[1]) == before


def test_ordinary_preboundary_activity_and_login_changes_do_not_invalidate_authority(db):
    approved = plan(db)
    stage(db, approved)
    with Session(db[1]) as s:
        s.add(m.CampPointAward(profile_id=24, activity_type="care", points_awarded=1,
                              occurred_at=BOUNDARY_AT-timedelta(seconds=1), duplicate_key="stage:care"))
        s.add(m.PracticeChart(profile_id=24, practice_date=date(2026, 10, 4), minutes=30,
                instrument="Trumpet", practice_details=[], team_id=None,
                created_at=BOUNDARY_AT-timedelta(seconds=1)))
        s.get(m.WoodchuckProfile, 24).display_name = "Changed display name"
        s.add(m.WoodchuckProfile(id=99, woodchuck_id="WC-STAGED-99", display_name="New user",
                pin_hash="synthetic", instrument="Trumpet", level="Beginner", goal="Practice"))
        s.commit()
    before_activation = snapshot(db[1])
    receipt = activate(db, approved)
    after = snapshot(db[1])
    assert after["camp_point_awards"] == before_activation["camp_point_awards"]
    assert after["practice_charts"] == before_activation["practice_charts"]
    assert all(row["team_id"] is None for row in after["practice_charts"] if row["practice_date"] == "2026-10-04")
    assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]


@pytest.mark.parametrize("change", ["membership", "moderation", "owner_status", "request", "future_week"])
def test_changed_staged_authority_fails_closed_at_boundary(db, change):
    approved = plan(db)
    stage(db, approved)
    with Session(db[1]) as s:
        if change == "membership":
            s.get(m.TeamMembership, 119).ended_at = NOW + timedelta(days=1)
        elif change == "moderation":
            s.get(m.Team, 10).moderation_status = "hidden"
        elif change == "owner_status":
            s.get(m.WoodchuckProfile, 1).status = "deleted"
        elif change == "request":
            s.add(m.TeamJoinRequest(season_id=2, team_id=10, profile_id=24))
        else:
            s.add(m.ContestWeek(season_id=3, week_start=date(2026, 10, 12), week_end=date(2026, 10, 19),
                status="open", verification_deadline_at=BOUNDARY_AT+timedelta(days=14),
                finalize_after=BOUNDARY_AT+timedelta(days=14)))
        s.commit()
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="staged_authority_changed"):
        activate(db, approved)
    assert snapshot(db[1]) == before
    assert not any(row["is_persistent"] for row in before["team_memberships"])


def test_changed_membership_requires_new_exact_approved_ids_and_can_be_reapproved(db):
    approved = plan(db)
    stage(db, approved)
    with Session(db[1]) as s:
        s.get(m.TeamMembership, 119).ended_at = NOW+timedelta(hours=1)
        s.flush()
        s.add(m.TeamMembership(id=200, profile_id=24, team_id=10, season_id=2,
                started_at=NOW+timedelta(hours=1), selected_week_start=date(2026, 9, 28)))
        s.commit()
    with pytest.raises(cutover.CutoverError, match="staged_authority_changed"):
        activate(db, approved)
    ids = [mid for mid in MEMBERSHIP_IDS if mid != 119] + [200]
    renewed = cutover.generate_plan(db[0], TEAM_IDS, ids, BOUNDARY, now=BOUNDARY_AT)
    assert renewed["content"]["supersedes_staged_plan_sha256"] == approved["plan_sha256"]
    stage(db, renewed, now=BOUNDARY_AT)
    receipt = activate(db, renewed)
    with Session(db[1]) as s:
        assert not s.get(m.TeamMembership, 119).is_persistent
        assert s.get(m.TeamMembership, 200).is_persistent
    assert cutover.verify_cutover(db[0], renewed, renewed["plan_sha256"], receipt)["passed"]


def test_existing_closing_roster_frozen_before_exact_boundary_changes(db):
    # A presentation season with the old membership rows demonstrates a nonempty
    # legacy roster; no row is relabeled by the cutover itself.
    with Session(db[1]) as s:
        s.get(m.ContestWeek, 2).season_id = 2
        s.commit()
    approved = plan(db)
    stage(db, approved)
    activate(db, approved)
    with Session(db[1]) as s:
        frozen = s.scalars(select(m.TeamWeekMembershipSnapshot).where(
            m.TeamWeekMembershipSnapshot.contest_week_id == 2)).all()
        assert len(frozen) == 20
        assert {r.membership_id for r in frozen} == set(MEMBERSHIP_IDS)
        assert s.get(m.ContestWeek, 2).team_roster_frozen_at is not None
        # Subsequent [start,end) change at exactly Monday cannot destroy the
        # closing legacy roster, even though the old interval no longer covers it.
        s.get(m.TeamMembership, 119).ended_at = BOUNDARY_AT
        s.flush()
        s.add(m.TeamMembership(profile_id=24, team_id=10, is_persistent=True,
                started_at=BOUNDARY_AT, selected_week_start=BOUNDARY))
        s.commit()
        from app.contests import _snapshot_memberships
        assert {r.profile_id: r.team_id for r in _snapshot_memberships(s, s.get(m.ContestWeek, 2))}[24] == 12
        from app.team_authority import effective_membership
        assert effective_membership(s, 24, BOUNDARY_AT-timedelta(microseconds=1)).team_id == 12
        assert effective_membership(s, 24, BOUNDARY_AT).team_id == 10


def test_empty_legacy_roster_is_frozen_explicitly(db):
    approved = plan(db)
    stage(db, approved)
    activate(db, approved)
    with Session(db[1]) as s:
        assert s.get(m.ContestWeek, 2).team_roster_frozen_at is not None
        assert s.scalars(select(m.TeamWeekMembershipSnapshot).where(
            m.TeamWeekMembershipSnapshot.contest_week_id == 2)).all() == []


@pytest.mark.parametrize("kind", ["chart_created_at", "chart_practice_date", "award"])
def test_postboundary_activity_is_never_silently_reinterpreted(db, kind):
    approved = plan(db)
    stage(db, approved)
    with Session(db[1]) as s:
        if kind == "award":
            s.add(m.CampPointAward(profile_id=24, activity_type="care", points_awarded=1,
                                  occurred_at=BOUNDARY_AT, duplicate_key="too-late"))
        else:
            s.add(m.PracticeChart(profile_id=24,
                practice_date=BOUNDARY if kind == "chart_practice_date" else date(2026, 10, 4),
                minutes=10, instrument="Trumpet", practice_details=[],
                created_at=BOUNDARY_AT if kind == "chart_created_at" else NOW))
        s.commit()
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="post_boundary_activity"):
        activate(db, approved)
    assert snapshot(db[1]) == before


def test_verify_cannot_be_forged_by_receipt_matching_unchanged_database(db):
    approved = plan(db)
    forged = {"transaction_state": "committed", "operation": "activated", "verification": {
        "passed": True, "approved_plan_sha256": approved["plan_sha256"],
        "after": cutover._evidence(snapshot(db[1])), "activated_at": BOUNDARY_AT.isoformat()}}
    with pytest.raises(cutover.CutoverError, match="activation_not_established"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], forged)
    stage(db, approved)
    forged["verification"]["after"] = cutover._evidence(snapshot(db[1]))
    with pytest.raises(cutover.CutoverError, match="activation_not_established"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], forged)


def test_activation_retry_refuses_after_success_without_changes(db):
    approved = plan(db)
    stage(db, approved)
    activate(db, approved)
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="disabled_staged_control"):
        activate(db, approved)
    assert snapshot(db[1]) == before
