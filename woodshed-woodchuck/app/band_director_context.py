"""Read-only team and contest context for an already-authorized roster member."""

from datetime import date, datetime, time, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .contests import (
    CENTRAL, central_week_boundaries, _membership_snapshot_at,
    weekly_camp_points, weekly_student_points,
)
from .models import Contest, ContestResult, ContestWeek, Season, Team, TeamMembership, TeamWeekMembershipSnapshot
from .seasons import season_covering_date
from .teams import active_membership, public_team_identity
from .team_authority import effective_membership, membership_for_week, persistent_enabled


def _contest_week_emblem(session: Session, *, profile_id: int, week: ContestWeek):
    """Resolve only this roster student's emblem, respecting frozen membership."""
    snapshot = session.scalar(select(TeamWeekMembershipSnapshot).where(
        TeamWeekMembershipSnapshot.contest_week_id == week.id,
        TeamWeekMembershipSnapshot.profile_id == profile_id,
    ))
    team_id = snapshot.team_id if snapshot is not None else None
    if snapshot is None and week.status != "finalized" and week.team_roster_frozen_at is None:
        membership = membership_for_week(
            session, profile_id=profile_id, week=week, at=_membership_snapshot_at(week),
        )
        team_id = membership.team_id if membership else None
    team = session.get(Team, team_id) if team_id is not None else None
    return public_team_identity(team)[1] if team is not None else None


def current_roster_period(session: Session, *, today: date):
    # Do not use ensure_band_camp_data(): a dashboard read must not create or
    # commit seasons, weeks, or contest definitions.
    season = season_covering_date(session, today)
    if season is None and not persistent_enabled(session):
        return None, None
    start, _, _, _ = central_week_boundaries(datetime.combine(today, time.min, CENTRAL))
    query = select(ContestWeek).where(ContestWeek.week_start == start)
    if not persistent_enabled(session):
        query = query.where(ContestWeek.season_id == season.id)
    week = session.scalars(query).one_or_none()
    return season, week


def current_student_teams(session: Session, *, profile_ids, season: Season | None,
                          at: datetime | None = None) -> dict[int, Team]:
    """Batch the current authorized roster without recomputing historical weeks."""
    if not profile_ids:
        return {}
    at = at or datetime.now(timezone.utc)
    persistent = persistent_enabled(session, at=at)
    if not persistent and season is None:
        return {}
    query = select(TeamMembership.profile_id, Team).join(
        Team, Team.id == TeamMembership.team_id,
    ).where(TeamMembership.profile_id.in_(profile_ids))
    if persistent:
        query = query.where(
            TeamMembership.is_persistent.is_(True), Team.is_operating.is_(True),
            TeamMembership.started_at <= at,
            or_(TeamMembership.ended_at.is_(None), TeamMembership.ended_at > at),
        )
    else:
        query = query.where(
            TeamMembership.season_id == season.id, Team.season_id == season.id,
            TeamMembership.ended_at.is_(None),
        )
    result = {}
    for profile_id, team in session.execute(query):
        if profile_id in result:
            raise ValueError("Ambiguous current Team membership authority.")
        result[profile_id] = team
    return result


def current_student_team(session: Session, *, profile_id: int, season: Season | None,
                         at: datetime | None = None) -> Team | None:
    """Current private roster identity is independent of the selected practice week."""
    at = at or datetime.now(timezone.utc)
    if persistent_enabled(session, at=at):
        membership = effective_membership(session, profile_id=profile_id, at=at)
        return session.get(Team, membership.team_id) if membership else None
    if season is None:
        return None
    membership = active_membership(session, profile_id=profile_id, season_id=season.id, at=at)
    team = session.get(Team, membership.team_id) if membership else None
    return team if team is not None and team.season_id == season.id else None


def student_contest_context(
    session: Session, *, profile_id: int, season: Season | None,
    week: ContestWeek | None,
) -> dict[str, object]:
    """Called only with IDs supplied by band_director_students; no public ID API."""
    team_data = None
    team = current_student_team(session, profile_id=profile_id, season=season)
    if team is not None:
        name, emblem = public_team_identity(team)
        team_data = {"name": name, "emblem": emblem}
    context = {"team": team_data, "contest": None}
    if week is None:
        return context

    positions = []
    if week.status == "finalized":
        # Persisted results are authoritative after finalization; do not rerank
        # historical results from later submissions or approvals.
        results = session.execute(select(ContestResult, Contest).join(
            Contest, Contest.id == ContestResult.contest_id,
        ).where(
            ContestResult.contest_week_id == week.id,
            ContestResult.profile_id == profile_id,
            ContestResult.subject_type == "student",
            ContestResult.division.in_(("open", "verified")),
            Contest.key.in_(("weekly-points-leaders", "weekly-camp-points")),
        ).order_by(Contest.key, ContestResult.division)).all()
        for result, contest in results:
            positions.append({
                "label": "Practice" if contest.key == "weekly-points-leaders" else "Board activity",
                "division": result.division, "rank": result.rank,
                "score": result.effective_score,
                "unit": "minutes" if contest.key == "weekly-points-leaders" else "points",
            })
    else:
        practice = weekly_student_points(session, contest_week=week, current_profile_id=profile_id)
        activity = weekly_camp_points(session, contest_week=week, current_profile_id=profile_id)
        for label, payload, score_key, unit in (
            ("Practice", practice, "total_minutes", "minutes"),
            ("Board activity", activity, "total_points", "points"),
        ):
            # Never pass the global leaderboard rows or names to the template.
            for division in ("open", "verified"):
                position = payload["current_user_position"].get(division)
                if position and position["has_score"]:
                    positions.append({
                        "label": label, "division": division,
                        "rank": position["rank"], "score": position[score_key], "unit": unit,
                    })

    context["contest"] = {
        "week_start": week.week_start.isoformat(),
        "status": week.status,
        "positions": positions,
        "emblem": _contest_week_emblem(session, profile_id=profile_id, week=week),
    }
    return context
