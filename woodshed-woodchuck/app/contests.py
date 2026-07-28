from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .account_routes import current_profile
from .db import SessionLocal
from .models import (
    Contest,
    ContestWeek,
    PracticeChart,
    PracticeChartVerification,
    Season,
    WoodchuckProfile,
)


BAND_CAMP_KEY = "band-camp-2026"
BAND_CAMP_NAME = "Band Camp"
BAND_CAMP_START = date(2026, 7, 27)
CENTRAL_TIMEZONE = "America/Chicago"
CENTRAL = ZoneInfo(CENTRAL_TIMEZONE)

CONTEST_DEFINITIONS = (
    {
        "key": "weekly-points-leaders",
        "name": "Top Five Points Leaders",
        "metric_type": "points",
        "subject_type": "student",
    },
    {
        "key": "weekly-practice-by-instrument",
        "name": "Weekly Practice Minutes by Instrument",
        "metric_type": "practice_minutes",
        "subject_type": "instrument",
    },
)

router = APIRouter(prefix="/contests", tags=["contests"])


def central_week_boundaries(
    now: datetime,
) -> tuple[date, date, datetime, datetime]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The current time must be timezone-aware.")

    central_now = now.astimezone(CENTRAL)
    week_start = central_now.date() - timedelta(days=central_now.weekday())
    week_end = week_start + timedelta(days=7)
    deadline_central = datetime.combine(week_end, time(hour=12), CENTRAL)
    verification_deadline_at = deadline_central.astimezone(timezone.utc)
    finalize_after = verification_deadline_at + timedelta(minutes=5)
    return week_start, week_end, verification_deadline_at, finalize_after


def ensure_band_camp_data(
    session: Session,
    *,
    now: datetime,
) -> tuple[Season, list[Contest], ContestWeek]:
    week_start, week_end, deadline, finalize_after = central_week_boundaries(now)

    season = session.scalar(select(Season).where(Season.key == BAND_CAMP_KEY))
    if season is None:
        season = Season(
            key=BAND_CAMP_KEY,
            name=BAND_CAMP_NAME,
            timezone=CENTRAL_TIMEZONE,
            starts_on=BAND_CAMP_START,
            status="active",
        )
        session.add(season)
        session.flush()

    contests: list[Contest] = []
    for definition in CONTEST_DEFINITIONS:
        contest = session.scalar(
            select(Contest).where(Contest.key == definition["key"])
        )
        if contest is None:
            contest = Contest(
                key=definition["key"],
                name=definition["name"],
                metric_type=definition["metric_type"],
                subject_type=definition["subject_type"],
                active=True,
            )
            session.add(contest)
        contests.append(contest)

    contest_week = session.scalar(
        select(ContestWeek).where(
            ContestWeek.season_id == season.id,
            ContestWeek.week_start == week_start,
        )
    )
    if contest_week is None:
        contest_week = ContestWeek(
            season_id=season.id,
            week_start=week_start,
            week_end=week_end,
            verification_deadline_at=deadline,
            finalize_after=finalize_after,
            status="open",
        )
        session.add(contest_week)

    session.commit()
    return season, contests, contest_week


def normalize_instrument(instrument: str) -> tuple[str, str]:
    display_name = " ".join(instrument.split()).title()
    return display_name.casefold(), display_name


def olympic_rankings(totals: dict[str, tuple[str, int]]) -> list[dict[str, object]]:
    ordered = sorted(
        totals.values(),
        key=lambda item: (-item[1], item[0].casefold(), item[0]),
    )
    rows: list[dict[str, object]] = []
    previous_total: int | None = None
    rank = 0

    for position, (instrument, total_minutes) in enumerate(ordered, start=1):
        if total_minutes != previous_total:
            rank = position
            previous_total = total_minutes
        rows.append(
            {
                "rank": rank,
                "instrument": instrument,
                "total_minutes": total_minutes,
            }
        )

    return rows


def weekly_practice_by_instrument(
    session: Session,
    *,
    contest_week: ContestWeek,
) -> dict[str, list[dict[str, object]]]:
    charts = session.scalars(
        select(PracticeChart).where(
            PracticeChart.practice_date >= contest_week.week_start,
            PracticeChart.practice_date < contest_week.week_end,
        )
    ).all()
    chart_ids = [chart.id for chart in charts]
    approved_chart_ids: set[int] = set()
    if chart_ids:
        approved_chart_ids = set(
            session.scalars(
                select(PracticeChartVerification.practice_chart_id).where(
                    PracticeChartVerification.practice_chart_id.in_(chart_ids),
                    PracticeChartVerification.status == "approved",
                )
            ).all()
        )

    open_totals: dict[str, tuple[str, int]] = {}
    verified_totals: dict[str, tuple[str, int]] = {}
    for chart in charts:
        key, display_name = normalize_instrument(chart.instrument)
        if not key:
            continue

        existing_open = open_totals.get(key, (display_name, 0))
        open_totals[key] = (existing_open[0], existing_open[1] + chart.minutes)

        if chart.id in approved_chart_ids:
            existing_verified = verified_totals.get(key, (display_name, 0))
            verified_totals[key] = (
                existing_verified[0],
                existing_verified[1] + chart.minutes,
            )

    return {
        "open": olympic_rankings(open_totals),
        "verified": olympic_rankings(verified_totals),
    }


def public_woodchuck_name(profile: WoodchuckProfile | None) -> str:
    if profile is None:
        return "Woodchuck"
    display_name = " ".join(profile.display_name.split())
    return display_name or "Woodchuck"


def student_points_rows(
    scores: dict[int, int],
    profiles: dict[int, WoodchuckProfile],
    *,
    current_profile_id: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    ordered = sorted(
        scores.items(),
        key=lambda item: (
            -item[1],
            public_woodchuck_name(profiles.get(item[0])).casefold(),
            public_woodchuck_name(profiles.get(item[0])),
        ),
    )
    all_rows: list[dict[str, object]] = []
    previous_score: int | None = None
    rank = 0
    for position, (profile_id, total_points) in enumerate(ordered, start=1):
        if total_points != previous_score:
            rank = position
            previous_score = total_points
        all_rows.append(
            {
                "rank": rank,
                "display_name": public_woodchuck_name(profiles.get(profile_id)),
                "total_points": total_points,
                "is_current_user": profile_id == current_profile_id,
            }
        )

    current_index = next(
        (
            index
            for index, row in enumerate(all_rows)
            if row["is_current_user"]
        ),
        None,
    )
    current_row = all_rows[current_index] if current_index is not None else None
    leader_points = int(all_rows[0]["total_points"]) if all_rows else 0
    current_points = int(current_row["total_points"]) if current_row else 0
    current_rank = int(current_row["rank"]) if current_row else None
    tied = bool(
        current_row
        and sum(
            row["total_points"] == current_row["total_points"]
            for row in all_rows
        ) > 1
    )
    in_top_five = current_index is not None and current_index < 5
    visible_rows = all_rows[:5]
    if current_row is not None and not in_top_five:
        visible_rows.append(current_row)

    return visible_rows, {
        "rank": current_rank,
        "total_points": current_points,
        "points_behind_leader": max(leader_points - current_points, 0),
        "tied": tied,
        "in_top_five": in_top_five,
        "has_score": current_row is not None,
    }


def weekly_student_points(
    session: Session,
    *,
    contest_week: ContestWeek,
    current_profile_id: int,
) -> dict[str, object]:
    charts = session.scalars(
        select(PracticeChart).where(
            PracticeChart.practice_date >= contest_week.week_start,
            PracticeChart.practice_date < contest_week.week_end,
        )
    ).all()
    chart_ids = [chart.id for chart in charts]
    approved_chart_ids: set[int] = set()
    if chart_ids:
        approved_chart_ids = set(
            session.scalars(
                select(PracticeChartVerification.practice_chart_id).where(
                    PracticeChartVerification.practice_chart_id.in_(chart_ids),
                    PracticeChartVerification.status == "approved",
                )
            ).all()
        )

    open_scores: dict[int, int] = {}
    verified_scores: dict[int, int] = {}
    for chart in charts:
        open_scores[chart.profile_id] = open_scores.get(chart.profile_id, 0) + 1
        if chart.id in approved_chart_ids:
            verified_scores[chart.profile_id] = (
                verified_scores.get(chart.profile_id, 0) + 1
            )

    profile_ids = set(open_scores) | set(verified_scores)
    profiles = {
        profile.id: profile
        for profile in session.scalars(
            select(WoodchuckProfile).where(WoodchuckProfile.id.in_(profile_ids))
        ).all()
    } if profile_ids else {}
    open_rows, open_position = student_points_rows(
        open_scores,
        profiles,
        current_profile_id=current_profile_id,
    )
    verified_rows, verified_position = student_points_rows(
        verified_scores,
        profiles,
        current_profile_id=current_profile_id,
    )
    return {
        "open": open_rows,
        "verified": verified_rows,
        "current_user_position": {
            "open": open_position,
            "verified": verified_position,
        },
    }


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def current_contests_payload(
    session: Session,
    *,
    now: datetime,
    current_profile_id: int,
) -> dict[str, object]:
    season, contests, contest_week = ensure_band_camp_data(session, now=now)
    standings = weekly_practice_by_instrument(
        session,
        contest_week=contest_week,
    )
    points_standings = weekly_student_points(
        session,
        contest_week=contest_week,
        current_profile_id=current_profile_id,
    )
    return {
        "season": {
            "key": season.key,
            "name": season.name,
            "timezone": season.timezone,
            "status": season.status,
            "starts_on": season.starts_on.isoformat(),
            "ends_on": season.ends_on.isoformat() if season.ends_on else None,
        },
        "current_week": {
            "week_start": contest_week.week_start.isoformat(),
            "week_end": contest_week.week_end.isoformat(),
            "verification_deadline_at": utc_iso(
                contest_week.verification_deadline_at
            ),
            "finalize_after": utc_iso(contest_week.finalize_after),
            "status": contest_week.status,
            "finalized_at": utc_iso(contest_week.finalized_at),
        },
        "contests": [
            {
                "key": contest.key,
                "name": contest.name,
                "metric_type": contest.metric_type,
                "subject_type": contest.subject_type,
            }
            for contest in contests
        ],
        "standings": {
            "weekly-points-leaders": points_standings,
            "weekly-practice-by-instrument": standings,
        },
    }


@router.get("/current")
def current_contests(request: Request) -> dict[str, object]:
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Student sign-in is required.",
            )
        return current_contests_payload(
            session,
            now=datetime.now(timezone.utc),
            current_profile_id=profile.id,
        )
