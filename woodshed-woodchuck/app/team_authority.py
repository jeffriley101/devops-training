"""Explicit persistent Team authority; historical readers remain versioned.

None of these helpers activates authority or manufactures a seasonal successor.
The caller owns the transaction, and writers acquire the singleton fence before
making an authority decision so cutover and membership transitions serialize.
"""
from datetime import datetime, timezone

from sqlalchemy import or_, select, text

from .models import PersistentTeamControl, Team, TeamMembership


LEGACY_RULES = "legacy_seasonal_v1"
PERSISTENT_RULES = "persistent_v1"


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def persistent_enabled(session, at=None):
    moment = utc(at or datetime.now(timezone.utc))
    with session.no_autoflush:
        control = session.get(PersistentTeamControl, 1, populate_existing=True)
    return bool(control is not None and control.activated_at is not None
                and utc(control.activated_at) <= moment)


def lock_authority(session):
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
        return session.scalar(select(PersistentTeamControl).where(
            PersistentTeamControl.id == 1
        ).with_for_update().execution_options(populate_existing=True))


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
    if (control is not None and control.activated_at is not None
            and control.rules_from_week_start is not None
            and week_start >= control.rules_from_week_start):
        return PERSISTENT_RULES
    return LEGACY_RULES


def membership_for_week(session, profile_id, week, at):
    if week_uses_persistent(week):
        return effective_membership(session, profile_id, at)
    moment = utc(at)
    return session.scalars(select(TeamMembership).where(
        TeamMembership.profile_id == profile_id,
        TeamMembership.season_id == week.season_id,
        TeamMembership.started_at <= moment,
        or_(TeamMembership.ended_at.is_(None), TeamMembership.ended_at > moment),
    ).order_by(TeamMembership.started_at.desc(), TeamMembership.id.desc())).first()
