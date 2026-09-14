"""Explicit seasonal continuity engine. No runtime entry point invokes apply.

H2 must run backdated application with finalization/calendar maintenance paused:
the frozen-data checks here are guardrails, not a replacement for that protocol.
The caller owns commit/rollback; a failed apply must roll back its transaction.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, text

from .models import (ContestResult, ContestWeek, DirectorTeamContest, Season,
                     Team, TeamFamily, TeamMembership, TeamWeekMembershipSnapshot,
                     WoodchuckProfile)


def lock_team_seasons(session, *season_ids):
    """Common writer fence, BEFORE team decisions/changes. Never commits.

    PostgreSQL READ COMMITTED: sorted season locks serialize participating
    writers, including predicates for rows that do not exist yet. SQLite uses
    the project's BEGIN IMMEDIATE convention when not already in a DB write
    transaction. Callers must not enter with an earlier SQLite read snapshot.
    """
    with session.no_autoflush:
        if session.get_bind().dialect.name == "sqlite":
            raw = session.connection().connection.driver_connection
            if not raw.in_transaction:
                session.execute(text("BEGIN IMMEDIATE"))
        return list(session.scalars(select(Season).where(
            Season.id.in_(sorted(set(season_ids)))
        ).order_by(Season.id).with_for_update().execution_options(populate_existing=True)))


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MemberAction:
    source_membership_id: int
    profile_id: int
    classification: str
    reason: str
    destination_membership_id: int | None = None


@dataclass(frozen=True)
class TeamAction:
    source_team_id: int
    family_id: int
    destination_season_id: int
    successor_team_id: int | None
    classification: str
    reasons: tuple[str, ...]
    members: tuple[MemberAction, ...]


@dataclass(frozen=True)
class ContinuityPlan:
    source_season_id: int
    destination_season_id: int
    boundary: datetime | None
    selected_week_start: date | None
    reasons: tuple[str, ...]
    teams: tuple[TeamAction, ...]

    @property
    def classification(self):
        states = [a.classification for a in self.teams]
        states += [m.classification for a in self.teams for m in a.members]
        if "membership_season_mismatch" in self.reasons or "CONFLICT" in states:
            return "CONFLICT"
        return "REVIEW" if self.reasons or "REVIEW" in states else "SAFE"


IDENTITY_FIELDS = ("display_name", "normalized_name", "emblem_key",
                   "creator_profile_id", "visibility", "director_led")


def plan_team_continuity(session, *, source_season_id, destination_season_id, now=None):
    """Read-only; classifications belong to individual Team/member actions.

    SAFE Team + CONFLICT member permits the other SAFE roster rows only.
    An already represented row is a no-op, even if it was subsequently ended.
    No writable plan objects or client-provided identity fields are accepted.
    """
    if session.new or session.dirty or session.deleted:
        raise ValueError("Continuity planning requires a clean unit of work.")
    with session.no_autoflush:
        return _plan(session, source_season_id, destination_season_id, _utc(now or datetime.now(timezone.utc)))


def _plan(session, source_id, destination_id, now):
    from .teams import has_band_director_capability

    def rows(model, *conditions):
        return list(session.scalars(select(model).where(*conditions).order_by(model.id)
                                   .execution_options(populate_existing=True)))

    source = session.get(Season, source_id, populate_existing=True)
    dest = session.get(Season, destination_id, populate_existing=True)
    reasons = []
    boundary = week_start = None
    if source is None or dest is None or source_id == destination_id:
        reasons.append("invalid_season_pair")
    else:
        # Gaps, overlaps and timezone changes require an explicit maintenance
        # decision; H1B does not invent a roster across an unknown interval.
        if (source.ends_on is None or source.ends_on + timedelta(days=1) != dest.starts_on
                or source.timezone != dest.timezone):
            reasons.append("nonadjacent_season_boundary")
        if dest.status == "closed":
            reasons.append("destination_closed")
        try:
            boundary = datetime.combine(dest.starts_on, time.min, ZoneInfo(dest.timezone)).astimezone(timezone.utc)
            week_start = dest.starts_on - timedelta(days=dest.starts_on.weekday())
            if boundary > now:
                reasons.append("boundary_not_reached")
        except (ValueError, KeyError):
            reasons.append("invalid_season_timezone")
        weeks = rows(ContestWeek, ContestWeek.season_id == dest.id)
        if (any(w.status == "finalized" or w.finalized_at is not None for w in weeks)
                or session.scalar(select(ContestResult.id).where(
                    ContestResult.contest_week_id.in_([w.id for w in weeks])).limit(1))
                or session.scalar(select(TeamWeekMembershipSnapshot.id).where(
                    TeamWeekMembershipSnapshot.contest_week_id.in_([w.id for w in weeks])).limit(1))):
            reasons.append("destination_competition_frozen")
        if rows(DirectorTeamContest, DirectorTeamContest.season_id == dest.id):
            reasons.append("destination_director_contest_review")
    if reasons:
        return ContinuityPlan(source_id, destination_id, boundary, week_start, tuple(reasons), ())

    sources = rows(Team, Team.season_id == source_id)
    destinations = rows(Team, Team.season_id == destination_id)
    # Include team/season inconsistencies instead of hiding them with a join.
    memberships = rows(TeamMembership, or_(TeamMembership.season_id == source_id,
                                         TeamMembership.team_id.in_([t.id for t in sources])))
    destination_history = rows(TeamMembership, or_(TeamMembership.season_id == destination_id,
                               TeamMembership.team_id.in_([t.id for t in destinations])))
    for membership in memberships + destination_history:
        team = session.get(Team, membership.team_id)
        if team is None or team.season_id != membership.season_id:
            return ContinuityPlan(source_id, destination_id, boundary, week_start,
                                  ("membership_season_mismatch",), ())
    boundary_rows = [m for m in memberships if _utc(m.started_at) < boundary
                     and (m.ended_at is None or _utc(m.ended_at) >= boundary)]
    actions = []
    for team in sources:
        review, conflicts = [], []
        successor = next((t for t in destinations if t.family_id == team.family_id), None)
        if team.moderation_status != "active":
            review.append("source_not_active")
        if session.get(TeamFamily, team.family_id) is None:
            conflicts.append("missing_family")
        owner = session.get(WoodchuckProfile, team.creator_profile_id, populate_existing=True) if team.creator_profile_id else None
        if team.visibility == "private":
            if (not team.director_led or owner is None or owner.status != "active"
                    or not has_band_director_capability(session, profile_id=owner.id)):
                review.append("private_owner_ineligible")
        elif team.visibility != "public" or team.director_led:
            conflicts.append("source_type_inconsistent")
        # A deleted public creator is normally anonymized to NULL by the account
        # deletion service. Do not silently change a non-null anomalous owner.
        if team.creator_profile_id and (owner is None or owner.status != "active"):
            review.append("creator_state_review")
        if successor:
            if (any(getattr(team, f) != getattr(successor, f) for f in IDENTITY_FIELDS)
                    or successor.moderation_status != "active"
                    or (team.visibility == "private" and
                        (not successor.join_code or successor.join_code == team.join_code))):
                conflicts.append("successor_identity_mismatch")
        for other in destinations:
            if successor and other.id == successor.id:
                continue
            if other.normalized_name == team.normalized_name:
                conflicts.append("destination_name_collision")
            if other.emblem_key == team.emblem_key:
                conflicts.append("destination_emblem_collision")
            if (team.visibility == other.visibility == "public" and team.creator_profile_id is not None
                    and other.creator_profile_id == team.creator_profile_id):
                conflicts.append("destination_creator_collision")
        members = []
        for m in boundary_rows:
            if m.team_id != team.id:
                continue
            history = [h for h in destination_history if h.profile_id == m.profile_id]
            represented = [h for h in history if successor and h.team_id == successor.id
                           and h.season_id == dest.id and _utc(h.started_at) == boundary
                           and h.selected_week_start == week_start]
            student = session.get(WoodchuckProfile, m.profile_id, populate_existing=True)
            duplicates = [h for h in boundary_rows if h.profile_id == m.profile_id]
            classification, reason, represented_id = "SAFE", "create_membership", None
            if m.season_id != source_id or any(h.season_id != dest.id for h in history):
                classification, reason = "CONFLICT", "membership_season_mismatch"
            elif len(duplicates) != 1:
                classification, reason = "REVIEW", "overlapping_source_memberships"
            elif m.ended_at is not None and _utc(m.ended_at) == boundary:
                classification, reason = "REVIEW", "exact_boundary_ending"
            elif student is None or student.status != "active":
                classification, reason = "REVIEW", "student_ineligible"
            elif len(represented) == 1:
                reason, represented_id = "already_represented", represented[0].id
            elif history:
                classification, reason = "CONFLICT", "destination_membership_history"
            members.append(MemberAction(m.id, m.profile_id, classification, reason, represented_id))
        if team.visibility == "public" and not any(m.classification == "SAFE" for m in members):
            review.append("no_safe_boundary_roster")
        classification = "CONFLICT" if conflicts else "REVIEW" if review else "SAFE"
        actions.append(TeamAction(team.id, team.family_id, dest.id,
            successor.id if successor else None, classification,
            tuple(sorted(set(conflicts + review))) or ("reuse_successor" if successor else "create_successor",),
            tuple(members)))
    return ContinuityPlan(source_id, destination_id, boundary, week_start, (), tuple(actions))


def lock_continuity_rows(session, *, source_season_id, destination_season_id):
    """Shared H1B lock order for apply and reviewed maintenance preconditions.

    Caller owns the transaction. Safe to reacquire within that same transaction.
    """
    if session.new or session.dirty or session.deleted:
        raise ValueError("Continuity requires a clean unit of work.")
    if (session.get_bind().dialect.name == "postgresql"
            and session.connection().get_isolation_level() != "READ COMMITTED"):
        raise ValueError("Continuity requires PostgreSQL READ COMMITTED isolation.")
    lock_team_seasons(session, source_season_id, destination_season_id)
    team_rows = list(session.scalars(select(Team).where(Team.season_id.in_(
        [source_season_id, destination_season_id])).order_by(Team.id)))
    for model, condition in (
        (TeamFamily, TeamFamily.id.in_([t.family_id for t in team_rows])),
        (Team, Team.id.in_([t.id for t in team_rows])),
    ):
        list(session.scalars(select(model).where(condition).order_by(model.id).with_for_update()
                             .execution_options(populate_existing=True)))
    member_rows = list(session.scalars(select(TeamMembership).where(or_(
        TeamMembership.season_id.in_([source_season_id, destination_season_id]),
        TeamMembership.team_id.in_([t.id for t in team_rows])))))
    profile_ids = {m.profile_id for m in member_rows} | {t.creator_profile_id for t in team_rows if t.creator_profile_id}
    list(session.scalars(select(WoodchuckProfile).where(WoodchuckProfile.id.in_(profile_ids))
        .order_by(WoodchuckProfile.id).with_for_update().execution_options(populate_existing=True)))
    list(session.scalars(select(TeamMembership).where(TeamMembership.id.in_([m.id for m in member_rows]))
        .order_by(TeamMembership.id).with_for_update().execution_options(populate_existing=True)))


def apply_team_continuity(session, *, source_season_id, destination_season_id, now=None):
    """Apply a fresh locked plan; caller commits or rolls back the whole unit.

    Require a clean unit of work, PostgreSQL READ COMMITTED (the project default)
    and no earlier row locks. Constraint failures abort rather than guessing.
    No provider calls, auto-activation, history edits, or family creation.
    """
    from .teams import _new_join_code

    lock_continuity_rows(session, source_season_id=source_season_id,
                         destination_season_id=destination_season_id)
    plan = plan_team_continuity(session, source_season_id=source_season_id,
                                destination_season_id=destination_season_id, now=now)
    applied = []
    for action in plan.teams:
        if action.classification != "SAFE":
            applied.append(action)
            continue
        successor = session.get(Team, action.successor_team_id) if action.successor_team_id else None
        if successor is None:
            source = session.get(Team, action.source_team_id)
            try:
                code = _new_join_code(session) if source.director_led else None
            except RuntimeError:
                applied.append(replace(action, classification="CONFLICT", reasons=("join_code_collision",)))
                continue
            successor = Team(season_id=destination_season_id, family_id=source.family_id,
                             **{f: getattr(source, f) for f in IDENTITY_FIELDS}, join_code=code)
            session.add(successor)
            session.flush()
        members = []
        for member in action.members:
            if member.classification == "SAFE" and member.destination_membership_id is None:
                row = TeamMembership(season_id=destination_season_id, team_id=successor.id,
                    profile_id=member.profile_id, selected_week_start=plan.selected_week_start,
                    started_at=plan.boundary)
                session.add(row)
                session.flush()
                member = replace(member, destination_membership_id=row.id)
            members.append(member)
        applied.append(replace(action, successor_team_id=successor.id, members=tuple(members)))
    return replace(plan, teams=tuple(applied))
