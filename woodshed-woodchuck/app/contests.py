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
) -> dict[str, object]:
    season, contests, contest_week = ensure_band_camp_data(session, now=now)
    standings = weekly_practice_by_instrument(
        session,
        contest_week=contest_week,
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
            "weekly-practice-by-instrument": standings,
        },
    }


@router.get("/current")
def current_contests(request: Request) -> dict[str, object]:
    with SessionLocal() as session:
        if current_profile(request, session) is None:
            raise HTTPException(
                status_code=401,
                detail="Student sign-in is required.",
            )
        return current_contests_payload(
            session,
            now=datetime.now(timezone.utc),
        )
