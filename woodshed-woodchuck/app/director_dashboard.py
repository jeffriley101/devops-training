from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .account_routes import current_profile
from .contests import CENTRAL, central_week_boundaries, ensure_band_camp_data
from .db import SessionLocal
from .models import (
    DirectorTeamContest,
    DirectorTeamContestEntry,
    DirectorTeamContestResult,
    PracticeChart,
    PracticeChartVerification,
    Team,
    TeamJoinRequest,
    TeamMembership,
    WoodchuckProfile,
)
from .team_practice_rating import (
    ACTIVE_MINUTES_THRESHOLD,
    calculate_team_practice_rating,
)
from .teams import (
    _owned_director_team,
    director_team_payload,
    emblem_payload,
    has_band_director_capability,
    public_team_identity,
)
from .verifier_routes import current_verifier


DIRECTOR_CONTEST_METRICS = {
    "total_minutes": "Total Practice Minutes",
    "average_minutes": "Average Practice Minutes",
    "team_practice_rating": "Team Practice Rating",
}
TEAM_MEMBER_MINUTES_CAP = 300

router = APIRouter(prefix="/director", tags=["band-director"])


class DirectorContestCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    starts_at: datetime
    ends_at: datetime
    finalizes_at: datetime
    metric: str
    team_ids: list[int] = Field(min_length=1, max_length=20)

    @field_validator("title", "description")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("metric")
    @classmethod
    def valid_metric(cls, value: str) -> str:
        if value not in DIRECTOR_CONTEST_METRICS:
            raise ValueError("Choose an available team contest metric.")
        return value

    @field_validator("starts_at", "ends_at", "finalizes_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Contest dates and times must include a timezone.")
        return value.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _director_profile(request: Request, session: Session) -> WoodchuckProfile:
    profile = current_profile(request, session)
    if profile is None:
        raise HTTPException(status_code=401, detail="Student sign-in is required.")
    if not has_band_director_capability(session, profile_id=profile.id):
        raise HTTPException(status_code=403, detail="Band Director authorization is required.")
    return profile


def _owned_team(
    session: Session, *, profile: WoodchuckProfile, team_id: int, season_id: int | None = None
) -> Team:
    try:
        team = _owned_director_team(session, profile=profile, team_id=team_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if season_id is not None and team.season_id != season_id:
        raise HTTPException(status_code=404, detail="Director-led team was not found.")
    return team


def _period_roster(
    session: Session, *, team_id: int, starts_at: datetime, ends_at: datetime
) -> set[int]:
    return set(session.scalars(select(TeamMembership.profile_id).join(
        WoodchuckProfile, WoodchuckProfile.id == TeamMembership.profile_id
    ).where(
        TeamMembership.team_id == team_id,
        TeamMembership.started_at < _utc(ends_at),
        or_(TeamMembership.ended_at.is_(None), TeamMembership.ended_at > _utc(starts_at)),
        WoodchuckProfile.status == "active",
    )).all())


def _dashboard_practice_rows(
    session: Session, *, team_id: int, week_start: date, week_end: date
) -> list[PracticeChart]:
    return list(session.scalars(select(PracticeChart).where(
        PracticeChart.team_id == team_id,
        PracticeChart.practice_date >= week_start,
        PracticeChart.practice_date < week_end,
        PracticeChart.include_contests.is_(True),
        PracticeChart.include_team_contests.is_(True),
    ).order_by(PracticeChart.practice_date, PracticeChart.id)).all())


def dashboard_payload(
    session: Session, *, request: Request, profile: WoodchuckProfile,
    team_id: int | None, now: datetime,
) -> dict[str, object]:
    season, _, contest_week = ensure_band_camp_data(session, now=now)
    management = director_team_payload(
        session, profile=profile, season=season, team_id=team_id
    )
    team_data = management.get("team")
    if not team_data:
        return {
            **management,
            "period": None,
            "metrics": None,
            "charts": {"daily_practice": [], "by_instrument": []},
            "verifier_queue_available": current_verifier(request, session) is not None,
            "contest_metrics": DIRECTOR_CONTEST_METRICS,
        }

    selected_id = int(team_data["id"])
    week_start, week_end = contest_week.week_start, contest_week.week_end
    starts_at = datetime.combine(week_start, time.min, CENTRAL).astimezone(timezone.utc)
    ends_at = datetime.combine(week_end, time.min, CENTRAL).astimezone(timezone.utc)
    roster = _period_roster(
        session, team_id=selected_id, starts_at=starts_at, ends_at=ends_at
    )
    charts = _dashboard_practice_rows(
        session, team_id=selected_id, week_start=week_start, week_end=week_end
    )
    charts = [chart for chart in charts if chart.profile_id in roster]
    member_minutes: dict[int, int] = defaultdict(int)
    daily_minutes: dict[date, int] = defaultdict(int)
    instrument_minutes: dict[str, int] = defaultdict(int)
    for chart in charts:
        member_minutes[chart.profile_id] += chart.minutes
        daily_minutes[chart.practice_date] += chart.minutes
        instrument = " ".join(chart.instrument.split()).title() or "Other"
        instrument_minutes[instrument] += chart.minutes
    capped = {
        member_id: min(minutes, TEAM_MEMBER_MINUTES_CAP)
        for member_id, minutes in member_minutes.items()
    }
    meaningful = [minutes for minutes in capped.values() if minutes >= ACTIVE_MINUTES_THRESHOLD]
    total_minutes = sum(capped.values())
    average_minutes = round(sum(meaningful) / len(meaningful), 1) if meaningful else 0.0
    tpr = calculate_team_practice_rating(
        list(member_minutes.values()), eligible_roster=len(roster)
    )

    chart_ids = [chart.id for chart in charts]
    verification_rows = list(session.scalars(select(PracticeChartVerification).where(
        PracticeChartVerification.practice_chart_id.in_(chart_ids)
    )).all()) if chart_ids else []
    verified_count = sum(row.status == "approved" for row in verification_rows)
    pending_count = sum(row.status == "pending" for row in verification_rows)

    today = _utc(now).astimezone(CENTRAL).date()
    final_day = min(today, week_end - timedelta(days=1))
    elapsed_days = max(0, (final_day - week_start).days + 1)
    meaningful_days = sum(
        daily_minutes.get(week_start + timedelta(days=offset), 0)
        >= ACTIVE_MINUTES_THRESHOLD
        for offset in range(elapsed_days)
    )
    consistency_rate = round(meaningful_days * 100 / elapsed_days) if elapsed_days else 0
    daily_rows = [
        {
            "date": (week_start + timedelta(days=offset)).isoformat(),
            "label": (week_start + timedelta(days=offset)).strftime("%a"),
            "minutes": daily_minutes.get(week_start + timedelta(days=offset), 0),
        }
        for offset in range(7)
    ]
    instrument_rows = [
        {"instrument": instrument, "minutes": minutes}
        for instrument, minutes in sorted(
            instrument_minutes.items(), key=lambda item: (-item[1], item[0].casefold())
        )
    ]
    return {
        **management,
        "period": {"week_start": week_start.isoformat(), "week_end": week_end.isoformat()},
        "metrics": {
            "total_practice_minutes": total_minutes,
            "average_minutes": average_minutes,
            "participation": {
                "active": len(meaningful),
                "eligible": len(roster),
                "percent": round(len(meaningful) * 100 / len(roster)) if roster else 0,
            },
            "p_charts": {
                "submitted": len(charts),
                "verified": verified_count,
                "pending": pending_count,
            },
            "consistency": {
                "days": meaningful_days,
                "elapsed_days": elapsed_days,
                "percent": consistency_rate,
            },
            "team_practice_rating": tpr.rating,
        },
        "charts": {"daily_practice": daily_rows, "by_instrument": instrument_rows},
        "verifier_queue_available": current_verifier(request, session) is not None,
        "contest_metrics": DIRECTOR_CONTEST_METRICS,
    }


def _contest_status(contest: DirectorTeamContest, now: datetime) -> str:
    if contest.status == "finalized":
        return "finalized"
    if _utc(now) < _utc(contest.starts_at):
        return "scheduled"
    if _utc(now) < _utc(contest.ends_at):
        return "open"
    return "pending"


def _result_payload(result: DirectorTeamContestResult) -> dict[str, object]:
    return {
        "rank": result.rank,
        "team_id": result.team_id,
        "team_name": result.team_name_snapshot,
        "emblem": emblem_payload(result.emblem_key_snapshot),
        "score": result.score,
        "active_participants": result.active_participant_count,
        "eligible_roster": result.eligible_roster_count,
    }


def director_contest_payload(
    session: Session, contest: DirectorTeamContest, *, now: datetime
) -> dict[str, object]:
    entries = session.scalars(select(DirectorTeamContestEntry).where(
        DirectorTeamContestEntry.contest_id == contest.id
    ).order_by(DirectorTeamContestEntry.id)).all()
    teams = {
        team.id: team for team in session.scalars(select(Team).where(
            Team.id.in_([entry.team_id for entry in entries])
        )).all()
    } if entries else {}
    results = session.scalars(select(DirectorTeamContestResult).where(
        DirectorTeamContestResult.contest_id == contest.id
    ).order_by(DirectorTeamContestResult.rank, DirectorTeamContestResult.team_name_snapshot)).all()
    return {
        "id": contest.id,
        "title": contest.title,
        "description": contest.description,
        "metric": contest.metric,
        "metric_label": DIRECTOR_CONTEST_METRICS[contest.metric],
        "starts_at": _utc(contest.starts_at).isoformat(),
        "ends_at": _utc(contest.ends_at).isoformat(),
        "finalizes_at": _utc(contest.finalizes_at).isoformat(),
        "status": _contest_status(contest, now),
        "finalized_at": _utc(contest.finalized_at).isoformat() if contest.finalized_at else None,
        "teams": [
            {
                "id": team.id,
                "name": public_team_identity(team)[0],
                "emblem": emblem_payload(team.emblem_key),
            }
            for entry in entries if (team := teams.get(entry.team_id)) is not None
        ],
        "results": [_result_payload(result) for result in results],
    }


def create_director_contest(
    session: Session, *, profile: WoodchuckProfile,
    submitted: DirectorContestCreate, now: datetime,
) -> DirectorTeamContest:
    if submitted.ends_at <= submitted.starts_at:
        raise ValueError("Contest end must be after its start.")
    if submitted.finalizes_at < submitted.ends_at:
        raise ValueError("Contest finalization cannot precede its end.")
    unique_team_ids = list(dict.fromkeys(submitted.team_ids))
    owned_teams = list(session.scalars(select(Team).where(
        Team.id.in_(unique_team_ids),
        Team.creator_profile_id == profile.id,
        Team.director_led.is_(True),
        Team.visibility == "private",
        Team.moderation_status != "hidden",
    )).all())
    if len(owned_teams) != len(unique_team_ids):
        raise PermissionError("A contest may include only director-led teams you manage.")
    season_ids = {team.season_id for team in owned_teams}
    if len(season_ids) != 1:
        raise ValueError("Participating teams must belong to the same season.")
    contest = DirectorTeamContest(
        season_id=next(iter(season_ids)),
        owner_profile_id=profile.id,
        title=submitted.title,
        description=submitted.description,
        metric=submitted.metric,
        starts_at=submitted.starts_at,
        ends_at=submitted.ends_at,
        finalizes_at=submitted.finalizes_at,
        status="open" if submitted.starts_at <= _utc(now) < submitted.ends_at else "scheduled",
        created_at=_utc(now),
        updated_at=_utc(now),
    )
    session.add(contest)
    session.flush()
    session.add_all([
        DirectorTeamContestEntry(contest_id=contest.id, team_id=team_id, created_at=_utc(now))
        for team_id in unique_team_ids
    ])
    session.commit()
    session.refresh(contest)
    return contest


def _contest_team_scores(
    session: Session, contest: DirectorTeamContest
) -> dict[int, tuple[float, int, int]]:
    team_ids = list(session.scalars(select(DirectorTeamContestEntry.team_id).where(
        DirectorTeamContestEntry.contest_id == contest.id
    )).all())
    totals: dict[int, dict[int, int]] = {team_id: defaultdict(int) for team_id in team_ids}
    rosters = {
        team_id: _period_roster(
            session, team_id=team_id,
            starts_at=contest.starts_at, ends_at=contest.ends_at,
        )
        for team_id in team_ids
    }
    charts = session.scalars(select(PracticeChart).where(
        PracticeChart.team_id.in_(team_ids),
        PracticeChart.include_contests.is_(True),
        PracticeChart.include_team_contests.is_(True),
        PracticeChart.created_at >= _utc(contest.starts_at),
        PracticeChart.created_at < _utc(contest.ends_at),
    )).all() if team_ids else []
    for chart in charts:
        if chart.profile_id in rosters.get(chart.team_id, set()):
            totals[chart.team_id][chart.profile_id] += chart.minutes

    scores: dict[int, tuple[float, int, int]] = {}
    for team_id in team_ids:
        member_values = list(totals[team_id].values())
        active_values = [
            min(minutes, TEAM_MEMBER_MINUTES_CAP)
            for minutes in member_values if minutes >= ACTIVE_MINUTES_THRESHOLD
        ]
        active_count = len(active_values)
        roster_count = len(rosters[team_id])
        if contest.metric == "total_minutes":
            score = float(sum(min(minutes, TEAM_MEMBER_MINUTES_CAP) for minutes in member_values))
        elif contest.metric == "average_minutes":
            score = round(sum(active_values) / active_count, 1) if active_count else 0.0
        else:
            score = calculate_team_practice_rating(
                member_values, eligible_roster=roster_count
            ).rating
        scores[team_id] = (score, active_count, roster_count)
    return scores


def finalize_director_contest(
    session: Session, *, contest: DirectorTeamContest, profile: WoodchuckProfile,
    now: datetime,
) -> tuple[DirectorTeamContest, bool]:
    if contest.owner_profile_id != profile.id:
        raise PermissionError("That director contest is not available.")
    if contest.status == "finalized":
        return contest, False
    now_utc = _utc(now)
    if now_utc < _utc(contest.finalizes_at):
        raise ValueError("This contest is not ready to finalize.")
    scores = _contest_team_scores(session, contest)
    teams = {
        team.id: team for team in session.scalars(select(Team).where(
            Team.id.in_(scores)
        )).all()
    } if scores else {}
    ordered = sorted(
        scores.items(), key=lambda item: (
            -item[1][0], public_team_identity(teams[item[0]])[0].casefold(), item[0]
        )
    )
    rank = 0
    previous_score: float | None = None
    for position, (team_id, (score, active_count, roster_count)) in enumerate(ordered, start=1):
        if score != previous_score:
            rank, previous_score = position, score
        team = teams[team_id]
        name, _ = public_team_identity(team)
        session.add(DirectorTeamContestResult(
            contest_id=contest.id,
            team_id=team.id,
            team_name_snapshot=name,
            emblem_key_snapshot=team.emblem_key,
            score=score,
            rank=rank,
            active_participant_count=active_count,
            eligible_roster_count=roster_count,
            created_at=now_utc,
        ))
    contest.status = "finalized"
    contest.finalized_at = now_utc
    contest.updated_at = now_utc
    session.commit()
    session.refresh(contest)
    return contest, True


@router.get("/dashboard")
def get_dashboard(request: Request, team_id: int | None = None):
    with SessionLocal() as session:
        profile = _director_profile(request, session)
        try:
            return dashboard_payload(
                session, request=request, profile=profile, team_id=team_id,
                now=datetime.now(timezone.utc),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/contests")
def list_director_contests(request: Request):
    with SessionLocal() as session:
        profile = _director_profile(request, session)
        now = datetime.now(timezone.utc)
        contests = session.scalars(select(DirectorTeamContest).where(
            DirectorTeamContest.owner_profile_id == profile.id
        ).order_by(DirectorTeamContest.starts_at.desc(), DirectorTeamContest.id.desc())).all()
        return {"contests": [director_contest_payload(session, row, now=now) for row in contests]}


@router.post("/contests", status_code=201)
def add_director_contest(request: Request, submitted: DirectorContestCreate):
    with SessionLocal() as session:
        profile = _director_profile(request, session)
        now = datetime.now(timezone.utc)
        try:
            contest = create_director_contest(
                session, profile=profile, submitted=submitted, now=now
            )
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"contest": director_contest_payload(session, contest, now=now)}


@router.post("/contests/{contest_id}/finalize")
def finalize_director_contest_route(contest_id: int, request: Request):
    with SessionLocal() as session:
        profile = _director_profile(request, session)
        contest = session.get(DirectorTeamContest, contest_id)
        if contest is None or contest.owner_profile_id != profile.id:
            raise HTTPException(status_code=404, detail="Director contest was not found.")
        now = datetime.now(timezone.utc)
        try:
            contest, created = finalize_director_contest(
                session, contest=contest, profile=profile, now=now
            )
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "created": created,
            "contest": director_contest_payload(session, contest, now=now),
        }
