"""Explicit persistent Team authority; historical readers remain versioned.

None of these helpers activates authority or manufactures a seasonal successor.
The caller owns the transaction, and writers acquire the singleton fence before
making an authority decision so cutover and membership transitions serialize.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from sqlalchemy import or_, select, text

from .models import PersistentTeamControl, Team, TeamMembership, TeamWeekMembershipSnapshot


LEGACY_RULES = "legacy_seasonal_v1"
PERSISTENT_RULES = "persistent_v1"


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _write_instant(session):
    operation = session.info.get("team_authority_operation")
    if (operation is not None and operation[0] is session.get_transaction()
            and operation[1] is session.get_nested_transaction()):
        return operation[2]
    session.info.pop("team_authority_operation", None)
    return None


def persistent_enabled(session, at=None):
    moment = utc(at or datetime.now(timezone.utc))
    with session.no_autoflush:
        control = session.get(PersistentTeamControl, 1, populate_existing=True)
    _require_boundary_ready(control, at=_write_instant(session))
    return bool(control is not None and control.activated_at is not None
                and utc(control.activated_at) <= moment)


def _require_boundary_ready(control, *, at=None):
    """No current operation may continue as legacy once a staged boundary is due.

    Reads use the wall clock. An admitted writer uses its transaction's fixed
    effective instant; all earning timestamps and date validation must use that
    same instant. The singleton remains locked until commit/rollback.
    """
    if (control is not None and control.activated_at is None
            and control.staged_for is not None
            and utc(control.staged_for) <= utc(at or datetime.now(timezone.utc))):
        raise HTTPException(status_code=503,
                            detail="Team transition is awaiting activation. Please try again shortly.")


def lock_authority(session, *, allow_pending=False):
    """Common cutover/current-writer fence; never insert, enable, or commit."""
    with session.no_autoflush:
        if session.get_bind().dialect.name == "sqlite":
            # create_all legacy fixtures intentionally have no control row.
            # The additive migration always seeds it, so deployed writers
            # still acquire the dormant fence before any cutover decision.
            if session.get(PersistentTeamControl, 1, populate_existing=True) is None:
                return None
            raw = session.connection().connection.driver_connection
            if not raw.in_transaction:
                session.execute(text("BEGIN IMMEDIATE"))
        control = session.scalar(select(PersistentTeamControl).where(
            PersistentTeamControl.id == 1
        ).with_for_update().execution_options(populate_existing=True))
        if not allow_pending:
            _require_boundary_ready(control, at=_write_instant(session))
        return control


def authority_write_time(session, *, at=None, clock=None, independent=False):
    """Admit a write after the common fence and fix its effective time.

    Sensitive writes linearize at admission, not at a later flush/commit. A
    Sunday operation may finish on Monday, but cannot validate Monday practice
    or store Monday earning timestamps. ACTIVATE waits for its transaction.
    Private excluded writes use the same lock order without the pending gate.
    ``at`` is for service callers already supplying an authoritative instant.
    A different explicit instant starts another operation in a caller-owned
    transaction and must pass admission again. Nested calls and HTTP/service
    clocks retain the admitted instant until commit/rollback.
    """
    existing = _write_instant(session)
    control = lock_authority(session, allow_pending=independent)
    if existing is not None and (at is None or utc(at) == existing):
        return existing
    moment = utc(at if at is not None else (clock or (lambda: datetime.now(timezone.utc)))())
    if not independent:
        _require_boundary_ready(control, at=moment)
        if (control is not None and control.activated_at is not None
                and moment < utc(control.activated_at)):
            raise HTTPException(409, "The Team authority changed. Retry with a current operation time.")
        session.info["team_authority_operation"] = (
            session.get_transaction(), session.get_nested_transaction(), moment)
    return moment


def practice_chart_team_id(session, *, profile_id, include_team_contests, at,
                           practice_date):
    """Resolve new chart attribution inside its admitted service transaction."""
    if not include_team_contests:
        return None
    from .contests import CENTRAL
    from .models import ContestWeek
    from .seasons import season_covering_date

    if persistent_enabled(session, at=at):
        today = at.astimezone(CENTRAL).date()
        week_start = today - timedelta(days=today.weekday())
        if practice_date < week_start:
            week = session.scalars(select(ContestWeek).where(
                ContestWeek.week_start <= practice_date, ContestWeek.week_end > practice_date,
            )).one_or_none()
            if week is None:
                raise HTTPException(409, "Team roster evidence is unavailable for that practice date.")
            if week.team_roster_frozen_at is not None or week.status == "finalized":
                return session.scalar(select(TeamWeekMembershipSnapshot.team_id).where(
                    TeamWeekMembershipSnapshot.contest_week_id == week.id,
                    TeamWeekMembershipSnapshot.profile_id == profile_id,
                ))
            if not week_uses_persistent(week):
                raise HTTPException(409, "Team roster evidence is unavailable for that practice date.")
            closed_at = datetime.combine(week.week_end, datetime.min.time(), CENTRAL).astimezone(timezone.utc)
            member = effective_membership(session, profile_id, closed_at - timedelta(microseconds=1))
        else:
            member = effective_membership(session, profile_id, at)
    else:
        season = season_covering_date(session, at.astimezone(CENTRAL).date())
        member = session.scalar(select(TeamMembership).where(
            TeamMembership.profile_id == profile_id, TeamMembership.season_id == season.id,
            TeamMembership.started_at <= at,
            or_(TeamMembership.ended_at.is_(None), TeamMembership.ended_at > at),
        )) if season is not None else None
    return member.team_id if member is not None else None


def effective_membership(session, profile_id, at):
    """Persistent marked authority at a half-open effective-time instant."""
    moment = utc(at)
    return session.scalars(select(TeamMembership).join(Team).where(
        TeamMembership.profile_id == profile_id,
        TeamMembership.is_persistent.is_(True),
        Team.is_operating.is_(True),
        TeamMembership.started_at <= moment,
        or_(TeamMembership.ended_at.is_(None), TeamMembership.ended_at > moment),
    )).one_or_none()


def week_uses_persistent(week):
    rules = week.team_membership_rules_version
    # SQLAlchemy column defaults have not run on an unflushed legacy fixture.
    if rules in (None, LEGACY_RULES):
        return False
    if rules == PERSISTENT_RULES:
        return True
    raise ValueError("Unknown Team membership rules version.")


def rules_version_for_start(session, week_start):
    """Choose rules only when provisioning a NEW week; never retag old rows."""
    with session.no_autoflush:
        control = session.get(PersistentTeamControl, 1, populate_existing=True)
    _require_boundary_ready(control, at=_write_instant(session))
    if (control is not None and control.activated_at is not None
            and control.rules_from_week_start is not None
            and week_start >= control.rules_from_week_start):
        return PERSISTENT_RULES
    return LEGACY_RULES


def membership_for_week(session, profile_id, week, at):
    if week.team_roster_frozen_at is not None:
        snapshot = session.scalar(select(TeamWeekMembershipSnapshot).where(
            TeamWeekMembershipSnapshot.contest_week_id == week.id,
            TeamWeekMembershipSnapshot.profile_id == profile_id,
        ))
        return session.get(TeamMembership, snapshot.membership_id) if snapshot and snapshot.membership_id else None
    if week_uses_persistent(week):
        return effective_membership(session, profile_id, at)
    moment = utc(at)
    return session.scalars(select(TeamMembership).where(
        TeamMembership.profile_id == profile_id,
        TeamMembership.season_id == week.season_id,
        TeamMembership.started_at <= moment,
        or_(TeamMembership.ended_at.is_(None), TeamMembership.ended_at > moment),
    ).order_by(TeamMembership.started_at.desc(), TeamMembership.id.desc())).first()
