"""H1B is an explicitly invoked engine, not season activation or repair."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (ContestWeek, ProfileCapability, Season, Team, TeamFamily,
                        TeamJoinRequest, TeamMembership, TeamReport, WoodchuckProfile)
from app.team_continuity import apply_team_continuity, plan_team_continuity, _utc
from app.teams import create_and_join_team, create_director_team, select_team, team_payload
from tests.team_factory import make_team
from tests.test_teams import profile

BEFORE = datetime(2026, 9, 10, 15, tzinfo=timezone.utc)
BOUNDARY = datetime(2026, 9, 14, 5, tzinfo=timezone.utc)


def seed(factory):
    with factory() as s:
        source = Season(key="band-camp-2026", name="Band Camp", starts_on=date(2026, 7, 27),
                        ends_on=date(2026, 9, 13), timezone="America/Chicago", status="active")
        dest = Season(key="back-to-school-2026", name="Back to School", starts_on=date(2026, 9, 14),
                      ends_on=date(2026, 9, 27), timezone="America/Chicago", status="active")
        s.add_all([source, dest])
        owner, student, other = (profile(s, i) for i in (1, 2, 3))
        s.add(ProfileCapability(profile_id=owner.id, capability="band_director"))
        s.commit()
        factory.ids = (source.id, dest.id, owner.id, student.id, other.id)


@pytest.fixture
def db(tmp_path):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def fk(c, _):
        c.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    seed(factory)
    yield factory
    engine.dispose()


def source_team(s, db, private=False):
    source_id, _, owner_id, _, _ = db.ids
    args = dict(profile=s.get(WoodchuckProfile, owner_id), season=s.get(Season, source_id),
                name="Brass Cats", emblem_key="emoji:cat", now=BEFORE)
    return create_director_team(s, **args) if private else create_and_join_team(s, **args)[0]


def run(s, db, apply=True):
    return (apply_team_continuity if apply else plan_team_continuity)(s,
        source_season_id=db.ids[0], destination_season_id=db.ids[1],
        now=datetime(2027, 1, 1, tzinfo=timezone.utc))


def count(s, model):
    return s.scalar(select(func.count()).select_from(model))


def add_member(s, team, who, *, start=BEFORE, end=None, week=date(2026, 9, 7)):
    row = TeamMembership(team_id=team.id, season_id=team.season_id, profile_id=who,
                         selected_week_start=week, started_at=start, ended_at=end)
    s.add(row)
    s.flush()
    return row


def dest_team(s, db, **changes):
    values = dict(season_id=db.ids[1], display_name="Other Team", normalized_name="other team",
                  emblem_key="emoji:dog", creator_profile_id=db.ids[4])
    values.update(changes)
    team = make_team(s, **values)
    s.add(team)
    s.flush()
    return team


def test_canonical_continuation_identity_history_and_read_only_plan(db):
    with db() as s:
        original = source_team(s, db)
        report = TeamReport(team_id=original.id, reporter_profile_id=db.ids[3], category="other", details="History")
        s.add(report); s.commit()
        before = {c.name: getattr(original, c.name) for c in Team.__table__.columns}
        old_member = s.scalar(select(TeamMembership))
        writes = []
        def record(c, cursor, statement, params, ctx, many):
            if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                writes.append(statement)
        event.listen(s.get_bind(), "before_cursor_execute", record)
        plan = run(s, db, False)
        assert plan == run(s, db, False)
        event.remove(s.get_bind(), "before_cursor_execute", record)
        assert writes == [] and plan.teams[0].classification == "SAFE"
        assert plan.boundary == BOUNDARY
        result = run(s, db); s.commit()
        successor = s.get(Team, result.teams[0].successor_team_id)
        assert successor.id != original.id and successor.family_id == original.family_id
        assert successor.season_id == db.ids[1] and successor.created_at != original.created_at
        for field in ("display_name", "normalized_name", "emblem_key", "creator_profile_id", "visibility", "director_led"):
            assert getattr(successor, field) == getattr(original, field)
        s.refresh(original)
        # SQLite normalizes loaded timestamps to naive; compare by UTC below.
        for key, value in before.items():
            actual = getattr(original, key)
            assert (_utc(actual) == _utc(value)) if isinstance(value, datetime) else actual == value
        assert old_member.ended_at is None and old_member.team_id == original.id
        new = s.get(TeamMembership, result.teams[0].members[0].destination_membership_id)
        assert _utc(new.started_at) == BOUNDARY and new.selected_week_start == date(2026, 9, 14)
        assert new.id != old_member.id and new.ended_at is None
        assert count(s, TeamFamily) == 1 and count(s, TeamReport) == 1
        assert "family_id" not in team_payload(successor)
        for _ in range(3):
            assert run(s, db).teams[0].members[0].reason == "already_represented"
            s.commit()
        assert count(s, Team) == 2 and count(s, TeamMembership) == 2


@pytest.mark.parametrize("state", ["hidden", "under_review"])
def test_moderation_never_carries(db, state):
    with db() as s:
        team = source_team(s, db); team.moderation_status = state; s.commit()
        plan = run(s, db); s.commit()
        assert plan.teams[0].classification == "REVIEW"
        assert "source_not_active" in plan.teams[0].reasons and count(s, Team) == 1


def test_empty_public_and_null_creator(db):
    with db() as s:
        team = source_team(s, db); team.creator_profile_id = None; s.commit()
        assert run(s, db, False).teams[0].classification == "SAFE"
        m = s.scalar(select(TeamMembership)); m.ended_at = BEFORE + timedelta(hours=1); s.commit()
        plan = run(s, db)
        assert plan.teams[0].classification == "REVIEW" and count(s, Team) == 1


@pytest.mark.parametrize("invalid", [None, "missing", "capability", "inactive"])
def test_private_empty_rotates_code_pending_not_carried(db, invalid):
    with db() as s:
        team = source_team(s, db, True)
        pending = TeamJoinRequest(season_id=team.season_id, team_id=team.id, profile_id=db.ids[3], status="pending")
        s.add(pending)
        if invalid == "missing":
            team.creator_profile_id = None
        elif invalid == "capability":
            s.delete(s.scalar(select(ProfileCapability)))
        elif invalid == "inactive":
            s.get(WoodchuckProfile, db.ids[2]).status = "deleted"
        s.commit()
        result = run(s, db); s.commit()
        assert count(s, TeamMembership) == 0 and count(s, TeamJoinRequest) == 1
        s.refresh(pending); assert pending.status == "pending" and pending.team_id == team.id
        if invalid:
            assert result.teams[0].classification == "REVIEW" and count(s, Team) == 1
        else:
            new = s.get(Team, result.teams[0].successor_team_id)
            assert new.join_code and new.join_code != team.join_code
            assert new.director_led and new.visibility == "private"


@pytest.mark.parametrize("ending,expected", [(-1, None), (0, "exact_boundary_ending"), (1, "create_membership"), (None, "create_membership")])
def test_boundary_memberships(db, ending, expected):
    with db() as s:
        source_team(s, db)
        m = s.scalar(select(TeamMembership))
        m.ended_at = None if ending is None else BOUNDARY + timedelta(seconds=ending)
        s.commit()
        plan = run(s, db)
        members = plan.teams[0].members
        if expected is None:
            assert not members
        else:
            assert members[0].reason == expected
        assert count(s, Team) == (2 if expected == "create_membership" else 1)


@pytest.mark.parametrize("ended", [False, True])
def test_any_destination_history_wins(db, ended):
    with db() as s:
        source_team(s, db)
        dest = dest_team(s, db)
        add_member(s, dest, db.ids[2], start=BOUNDARY, end=BOUNDARY + timedelta(hours=1) if ended else None)
        s.commit()
        plan = run(s, db)
        assert plan.teams[0].members[0].reason == "destination_membership_history"
        assert count(s, TeamMembership) == 2 and count(s, Team) == 2


@pytest.mark.parametrize("field,value,reason", [
    ("normalized_name", "brass cats", "destination_name_collision"),
    ("emblem_key", "emoji:cat", "destination_emblem_collision"),
    ("creator_profile_id", 1, "destination_creator_collision"),
])
def test_collisions(db, field, value, reason):
    with db() as s:
        source_team(s, db)
        dest_team(s, db, **{field: value}); s.commit()
        action = run(s, db).teams[0]
        assert action.classification == "CONFLICT" and reason in action.reasons
        assert count(s, Team) == 2


def test_successor_mismatch_and_stale_plan_rechecked(db):
    with db() as s:
        original = source_team(s, db)
        assert run(s, db, False).teams[0].classification == "SAFE"
        s.commit()
        with db() as other:
            dest = dest_team(other, db)
            dest.family_id = original.family_id
            other.commit()
        result = run(s, db)
        assert "successor_identity_mismatch" in result.teams[0].reasons
        assert count(s, Team) == 2 and count(s, TeamMembership) == 1


@pytest.mark.parametrize("day", [14, 16])
def test_one_opening_week_correction_then_next_monday(db, day):
    with db() as s:
        source_team(s, db)
        source, dest = s.get(Season, db.ids[0]), s.get(Season, db.ids[1])
        source.ends_on = date(2026, 9, day - 1); dest.starts_on = date(2026, 9, day)
        other = dest_team(s, db); s.commit()
        result = run(s, db); s.commit()
        assert result.selected_week_start == date(2026, 9, 14)
        continued = s.get(Team, result.teams[0].successor_team_id)
        student = s.get(WoodchuckProfile, db.ids[2])
        args = dict(profile=student, season=dest, now=result.boundary + timedelta(hours=10))
        select_team(s, team=other, **args); s.commit()
        with pytest.raises(ValueError, match="locked"):
            select_team(s, team=continued, **args)
        s.rollback()
        assert run(s, db).teams[0].members[0].reason == "already_represented"
        s.commit()
        select_team(s, team=continued, **{**args, "now": datetime(2026, 9, 21, 15, tzinfo=timezone.utc)})
        s.commit()
        assert count(s, TeamMembership) == 4


def test_partial_roster_conflict(db):
    with db() as s:
        team = source_team(s, db)
        add_member(s, team, db.ids[3])
        other = dest_team(s, db); add_member(s, other, db.ids[3], start=BOUNDARY)
        s.commit()
        result = run(s, db); s.commit()
        assert result.teams[0].classification == "SAFE"
        assert [m.classification for m in result.teams[0].members] == ["SAFE", "CONFLICT"]
        assert count(s, TeamMembership) == 4


def test_overlap_is_review_and_invalid_season_is_conflict(db):
    with db() as s:
        team = source_team(s, db)
        duplicate = add_member(s, team, db.ids[2], start=BEFORE - timedelta(hours=1), end=BOUNDARY + timedelta(hours=1))
        s.commit()
        plan = run(s, db)
        assert all(m.reason == "overlapping_source_memberships" for m in plan.teams[0].members)
        assert count(s, Team) == 1
        duplicate.season_id = db.ids[1]; s.commit()
        plan = run(s, db)
        assert plan.classification == "CONFLICT" and plan.reasons == ("membership_season_mismatch",)


def test_frozen_destination_guard_and_transaction_rollback(db):
    with db() as s:
        source_team(s, db)
        run(s, db); s.rollback()
        assert count(s, Team) == count(s, TeamMembership) == 1
        s.add(ContestWeek(season_id=db.ids[1], week_start=date(2026, 9, 14), week_end=date(2026, 9, 21),
            verification_deadline_at=BOUNDARY, finalize_after=BOUNDARY, status="finalized"))
        s.commit()
        assert run(s, db).reasons == ("destination_competition_frozen",)
        assert count(s, Team) == 1


def test_join_code_collision_and_no_automatic_activation(db, monkeypatch):
    from app import teams
    with db() as s:
        source_team(s, db, True)
        def exhausted(session):
            raise RuntimeError("no code")
        monkeypatch.setattr(teams, "_new_join_code", exhausted)
        assert run(s, db).teams[0].reasons == ("join_code_collision",)
        assert count(s, Team) == 1
    from pathlib import Path
    for path in Path("app").glob("*.py"):
        # Explicit maintenance/jobs are allowed; web runtime stays dormant.
        if path.name not in {"team_continuity.py", "team_continuity_repair.py", "season_team_activation.py"}:
            assert "apply_team_continuity" not in path.read_text()
            assert "team_continuity_repair" not in path.read_text()


def test_all_historical_attribution_rewards_and_hall_unchanged(db):
    from app.contests import lifetime_team_identity
    from app.models import (PracticeChart, CampPointAward, ContestResult, TeamWeekMembershipSnapshot,
                            RewardGrant, CrownAward, CrownProgress)
    from tests.test_canonical_seasons import add_history
    with db() as s:
        team = source_team(s, db)
        week = ContestWeek(season_id=db.ids[0], week_start=date(2026, 9, 7), week_end=date(2026, 9, 14),
                          verification_deadline_at=BOUNDARY, finalize_after=BOUNDARY, status="open")
        s.add(week); s.commit()
        student, snapshot, result, *_ = add_history(s, week)
        snapshot.team_id = team.id
        result.team_id = team.id
        result.subject_type = "team"
        result.subject_key = str(team.id)
        add_member(s, team, student.id)
        s.add_all([
            PracticeChart(profile_id=student.id, practice_date=BEFORE.date(), minutes=30,
                          instrument="Flute", team_id=team.id),
            CampPointAward(profile_id=student.id, activity_type="test", points_awarded=5,
                           occurred_at=BEFORE, duplicate_key="continuity-history", team_id=team.id),
            # Already-stored destination activity without a team stays that way.
            PracticeChart(profile_id=student.id, practice_date=date(2026, 9, 14), minutes=20,
                          instrument="Flute", team_id=None),
        ])
        s.commit()
        models = (PracticeChart, CampPointAward, ContestResult, TeamWeekMembershipSnapshot,
                  RewardGrant, CrownAward, CrownProgress, ContestWeek, TeamReport, TeamJoinRequest)
        def history():
            return {m.__tablename__: list(s.execute(select(m.__table__).order_by(m.id))) for m in models}
        before = history()
        identity = lifetime_team_identity(result, team)
        applied = run(s, db); s.commit()
        assert history() == before
        new = s.get(Team, applied.teams[0].successor_team_id)
        assert lifetime_team_identity(ContestResult(team_id=new.id, subject_key=str(new.id)), new) == identity
        assert count(s, TeamMembership) == 4
        assert s.scalar(select(ContestWeek).where(ContestWeek.season_id == db.ids[1])) is None


def test_deleted_public_creator_with_other_active_roster(db):
    from app.account_deletion import anonymize_woodchuck_account
    with db() as s:
        team = source_team(s, db)
        add_member(s, team, db.ids[3]); s.commit()
        anonymize_woodchuck_account(s, profile=s.get(WoodchuckProfile, db.ids[2]), now=BEFORE)
        s.commit()
        result = run(s, db); s.commit()
        assert result.teams[0].classification == "SAFE"
        new = s.get(Team, result.teams[0].successor_team_id)
        assert new.creator_profile_id is None
        assert [m.profile_id for m in result.teams[0].members] == [db.ids[3]]


def test_future_started_membership_is_not_boundary_roster(db):
    with db() as s:
        source_team(s, db)
        m = s.scalar(select(TeamMembership)); m.started_at = BOUNDARY
        s.commit()
        assert run(s, db).teams[0].members == ()
        assert count(s, Team) == 1


def test_private_accepted_roster_carries_but_owner_and_pending_do_not(db):
    with db() as s:
        team = source_team(s, db, True)
        add_member(s, team, db.ids[3])
        s.add(TeamJoinRequest(team_id=team.id, season_id=team.season_id,
                             profile_id=db.ids[4], status="pending"))
        s.commit()
        result = run(s, db); s.commit()
        new_rows = list(s.scalars(select(TeamMembership).where(TeamMembership.season_id == db.ids[1])))
        assert [m.profile_id for m in new_rows] == [db.ids[3]]
        assert new_rows[0].team_id == result.teams[0].successor_team_id
        assert count(s, TeamJoinRequest) == 1


def test_compatible_existing_successor_reused_without_rewrite(db):
    from app.team_continuity import IDENTITY_FIELDS
    with db() as s:
        old = source_team(s, db)
        new = Team(season_id=db.ids[1], family_id=old.family_id,
                   **{field: getattr(old, field) for field in IDENTITY_FIELDS})
        s.add(new); s.commit()
        new_id = new.id
        result = run(s, db); s.commit()
        assert result.teams[0].successor_team_id == new_id
        assert result.teams[0].reasons == ("reuse_successor",)
        assert count(s, Team) == count(s, TeamMembership) == 2


def test_timezone_boundary_after_dst_and_closed_or_nonadjacent_guards(db):
    with db() as s:
        source_team(s, db)
        source, dest = s.get(Season, db.ids[0]), s.get(Season, db.ids[1])
        source.ends_on = date(2026, 11, 1)
        dest.starts_on = date(2026, 11, 2); dest.ends_on = date(2026, 11, 8)
        s.commit()
        assert run(s, db, False).boundary == datetime(2026, 11, 2, 6, tzinfo=timezone.utc)
        dest.status = "closed"; s.commit()
        assert run(s, db).reasons == ("destination_closed",)
        dest.status = "active"; source.ends_on = date(2026, 10, 31); s.commit()
        assert run(s, db).reasons == ("nonadjacent_season_boundary",)


def test_pending_dirty_session_not_flushed_or_discarded_by_plan(db):
    with db() as s:
        team = source_team(s, db)
        team.display_name = "Pending edit"
        with pytest.raises(ValueError, match="clean"):
            run(s, db, False)
        assert team.display_name == "Pending edit" and team in s.dirty


def test_future_boundary_cannot_be_known_yet(db):
    with db() as s:
        source_team(s, db)
        plan = apply_team_continuity(s, source_season_id=db.ids[0], destination_season_id=db.ids[1],
                                     now=BOUNDARY - timedelta(microseconds=1))
        assert plan.reasons == ("boundary_not_reached",) and count(s, Team) == 1


def test_apply_failure_rolls_back_successor_and_members(db):
    with db() as s:
        source_team(s, db)
        def fail(c, cursor, statement, params, context, many):
            if statement.lstrip().upper().startswith("INSERT INTO TEAM_MEMBERSHIPS"):
                raise RuntimeError("simulated membership persistence failure")
        event.listen(s.get_bind(), "before_cursor_execute", fail)
        try:
            with pytest.raises(RuntimeError, match="persistence"):
                run(s, db)
        finally:
            s.rollback()
            event.remove(s.get_bind(), "before_cursor_execute", fail)
        assert count(s, Team) == count(s, TeamMembership) == 1
        run(s, db); s.commit()
        assert count(s, Team) == count(s, TeamMembership) == 2
