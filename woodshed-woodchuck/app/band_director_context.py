"""Read-only team and contest context for an already-authorized roster member."""

from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contests import (
    CENTRAL, central_week_boundaries, contest_season_clause,
    weekly_camp_points, weekly_student_points,
)
from .models import Contest, ContestResult, ContestWeek, Season, Team, TeamWeekMembershipSnapshot
from .teams import active_membership, membership_at, public_team_identity


def _contest_week_emblem(session: Session, *, profile_id: int, week: ContestWeek):
    """Resolve only this roster student's emblem, respecting frozen membership."""
    snapshot = session.scalar(select(TeamWeekMembershipSnapshot).where(
        TeamWeekMembershipSnapshot.contest_week_id == week.id,
        TeamWeekMembershipSnapshot.profile_id == profile_id,
    ))
    team_id = snapshot.team_id if snapshot is not None else None
    if snapshot is None and week.status != "finalized":
        membership = membership_at(
            session, profile_id=profile_id, season_id=week.season_id,
            at=datetime.combine(week.week_end, time.min, CENTRAL).astimezone(timezone.utc),
        )
        team_id = membership.team_id if membership else None
    team = session.get(Team, team_id) if team_id is not None else None
    return public_team_identity(team)[1] if team is not None else None


def current_roster_period(session: Session, *, today: date):
    # Do not use ensure_band_camp_data(): a dashboard read must not create or
    # commit seasons, weeks, or contest definitions.
    season = session.scalar(select(Season).where(
        Season.status == "active", contest_season_clause(),
        Season.starts_on <= today,
        (Season.ends_on.is_(None) | (Season.ends_on >= today)),
    ).order_by(Season.starts_on.desc()))
    if season is None:
        return None, None
    start, _, _, _ = central_week_boundaries(datetime.combine(today, time.min, CENTRAL))
    week = session.scalar(select(ContestWeek).where(
        ContestWeek.season_id == season.id, ContestWeek.week_start == start,
    ))
    return season, week


def student_contest_context(
    session: Session, *, profile_id: int, season: Season | None,
    week: ContestWeek | None,
) -> dict[str, object]:
    """Called only with IDs supplied by band_director_students; no public ID API."""
    team_data = None
    if season is not None:
        membership = active_membership(session, profile_id=profile_id, season_id=season.id)
        team = session.get(Team, membership.team_id) if membership else None
        if team is not None and team.season_id == season.id:
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
                "score": result.score,
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
