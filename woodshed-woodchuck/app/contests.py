from __future__ import annotations

import hmac
import os
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_routes import current_profile
from .content import QUEST_POOL
from .db import SessionLocal
from .instruments import INSTRUMENTS_BY_LABEL
from .models import (
    CampPointAward,
    DailyTriviaAttempt,
    Contest,
    ContestResult,
    ContestWeek,
    CrownProgress,
    PracticeChart,
    PracticeChartVerification,
    QuestCompletion,
    RewardGrant,
    Season,
    WoodchuckProfile,
    WoodchuckState,
)


BAND_CAMP_KEY = "band-camp-2026"
BAND_CAMP_NAME = "Band Camp"
BAND_CAMP_START = date(2026, 7, 27)
CENTRAL_TIMEZONE = "America/Chicago"
CENTRAL = ZoneInfo(CENTRAL_TIMEZONE)

CONTEST_DEFINITIONS = (
    {
        "key": "weekly-points-leaders",
        "name": "Top Five Minutes Leaders",
        "metric_type": "practice_minutes",
        "subject_type": "student",
        "crown_category": "weekly-points-leaders",
    },
    {
        "key": "weekly-practice-by-instrument",
        "name": "Weekly Practice Minutes by Instrument",
        "metric_type": "practice_minutes",
        "subject_type": "instrument",
        "crown_category": None,
    },
    {
        "key": "weekly-camp-points",
        "name": "Weekly Band Camp Points",
        "metric_type": "points",
        "subject_type": "student",
        "crown_category": None,
    },
)

CAMP_POINT_ACTIVITIES = frozenset({"hours", "care", "trivia", "marching"})
CROWN_CATEGORIES = (
    ("weekly-points-leaders", "Practice Crown"),
    ("weekly-camp-points", "Band Camp Crown"),
    ("trivia", "Trivia Crown"),
    ("instrument-care", "Instrument Care Crown"),
    ("marching", "Marching Crown"),
    ("band-camp-hours", "Band Camp Hours Crown"),
)
ACTIVITY_CROWN_KEYS = {
    "trivia": "trivia", "care": "instrument-care",
    "marching": "marching", "hours": "band-camp-hours",
}

router = APIRouter(prefix="/contests", tags=["contests"])


class CampPointAwardCreate(BaseModel):
    activity_type: str
    activity_date: date


class TriviaAnswerSubmission(BaseModel):
    activity_date: date
    selected_answer_id: str


class QuestCompletionSubmission(BaseModel):
    activity_date: date
    quest_id: str = Field(min_length=1, max_length=100)
    minutes: int = Field(ge=1, le=1440)
    logged_minutes: int = Field(ge=1, le=1440)
    note: str = Field(default="", max_length=500)


TRIVIA_QUESTIONS = (
    {"id": "whole-note-44", "question": "How many beats does a whole note receive in 4/4 time?", "choices": (
        {"id": "two", "text": "2"}, {"id": "three", "text": "3"}, {"id": "four", "text": "4"},
    ), "correct_answer_id": "four"},
    {"id": "gradually-louder", "question": "Which word means to gradually get louder?", "choices": (
        {"id": "crescendo", "text": "Crescendo"}, {"id": "diminuendo", "text": "Diminuendo"}, {"id": "fermata", "text": "Fermata"},
    ), "correct_answer_id": "crescendo"},
    {"id": "conductor-upbeat", "question": "What does a conductor’s upbeat usually help signal?", "choices": (
        {"id": "entrance", "text": "An entrance"}, {"id": "break", "text": "A break"}, {"id": "rehearsal-end", "text": "The end of rehearsal"},
    ), "correct_answer_id": "entrance"},
    {"id": "stronger-wind-tone", "question": "What should most wind players use for a stronger tone?", "choices": (
        {"id": "less-air", "text": "Less air"}, {"id": "more-air", "text": "More air"}, {"id": "tighter-stand", "text": "A tighter music stand"},
    ), "correct_answer_id": "more-air"},
    {"id": "piano-marking", "question": "What does the marking piano mean?", "choices": (
        {"id": "softly", "text": "Play softly"}, {"id": "quickly", "text": "Play quickly"}, {"id": "stop", "text": "Stop playing"},
    ), "correct_answer_id": "softly"},
    {"id": "brass-section", "question": "Which section usually includes trumpets and trombones?", "choices": (
        {"id": "woodwinds", "text": "Woodwinds"}, {"id": "brass", "text": "Brass"}, {"id": "percussion", "text": "Percussion"},
    ), "correct_answer_id": "brass"},
    {"id": "metronome-purpose", "question": "What does a metronome help a musician maintain?", "choices": (
        {"id": "tempo", "text": "Tempo"}, {"id": "instrument-color", "text": "Instrument color"}, {"id": "stand-height", "text": "Music-stand height"},
    ), "correct_answer_id": "tempo"},
)


def trivia_question_for(activity_date: date) -> dict[str, object]:
    return TRIVIA_QUESTIONS[
        activity_date.timetuple().tm_yday % len(TRIVIA_QUESTIONS)
    ]


def public_trivia_question(activity_date: date) -> dict[str, object]:
    question = trivia_question_for(activity_date)
    return {
        "id": question["id"],
        "question": question["question"],
        "choices": [dict(choice) for choice in question["choices"]],
    }


def trivia_selected_choice(
    activity_date: date, stored_answer: object, *, correct: bool
) -> dict[str, str] | None:
    """Resolve stable IDs and unambiguous legacy answer text."""
    question = trivia_question_for(activity_date)
    choices = question["choices"]
    if not isinstance(stored_answer, str):
        return None
    value = stored_answer.strip()
    candidates = [choice for choice in choices if value in (choice["id"], choice["text"])]
    if value.isdigit():
        legacy_index = int(value)
        if 0 <= legacy_index < len(choices):
            candidates.append(choices[legacy_index])
    matching = {
        choice["id"]: choice for choice in candidates
        if (choice["id"] == question["correct_answer_id"]) is correct
    }
    if len(matching) == 1:
        return dict(next(iter(matching.values())))
    return None


def central_week_boundaries(
    now: datetime,
) -> tuple[date, date, datetime, datetime]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The current time must be timezone-aware.")

    central_now = now.astimezone(CENTRAL)
    week_start = central_now.date() - timedelta(days=central_now.weekday())
    week_end, verification_deadline_at, finalize_after = contest_week_schedule(
        week_start
    )
    return week_start, week_end, verification_deadline_at, finalize_after


def contest_week_schedule(
    week_start: date,
) -> tuple[date, datetime, datetime]:
    if week_start.weekday() != 0:
        raise ValueError("Contest weeks must start on Monday.")
    week_end = week_start + timedelta(days=7)
    deadline_central = datetime.combine(week_end, time(hour=12), CENTRAL)
    verification_deadline_at = deadline_central.astimezone(timezone.utc)
    return (
        week_end,
        verification_deadline_at,
        verification_deadline_at + timedelta(minutes=5),
    )


def ensure_contest_definitions(session: Session) -> list[Contest]:
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
                crown_category=definition["crown_category"],
                active=True,
            )
            session.add(contest)
        else:
            contest.name = definition["name"]
            contest.metric_type = definition["metric_type"]
        contests.append(contest)
    return contests


def ensure_band_camp_data(
    session: Session,
    *,
    now: datetime,
) -> tuple[Season, list[Contest], ContestWeek]:
    week_start, week_end, deadline, finalize_after = central_week_boundaries(now)

    central_today = now.astimezone(CENTRAL).date()
    season = session.scalar(
        select(Season).where(
            Season.status == "active",
            Season.key.like("band-camp-%"),
            Season.starts_on <= central_today,
            (Season.ends_on.is_(None) | (Season.ends_on >= central_today)),
        ).order_by(Season.starts_on.desc())
    )
    if season is None:
        season = session.scalar(select(Season).where(
            Season.key == BAND_CAMP_KEY,
            Season.status == "active",
        ))
    if season is None:
        existing_legacy = session.scalar(
            select(Season.id).where(Season.key == BAND_CAMP_KEY)
        )
        if existing_legacy is not None:
            raise HTTPException(
                status_code=409,
                detail="No active Band Camp season covers the current date.",
            )
        season = Season(
            key=BAND_CAMP_KEY,
            name=BAND_CAMP_NAME,
            timezone=CENTRAL_TIMEZONE,
            starts_on=BAND_CAMP_START,
            status="active",
        )
        session.add(season)
        session.flush()

    if (
        central_today < season.starts_on
        or (season.ends_on is not None and central_today > season.ends_on)
    ):
        raise HTTPException(
            status_code=409,
            detail="No active Band Camp season covers the current date.",
        )

    contests = ensure_contest_definitions(session)

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
    charts, approved_chart_ids = _charts_and_approved_ids(session, contest_week)

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


def student_score_rows(
    scores: dict[int, int],
    profiles: dict[int, WoodchuckProfile],
    *,
    current_profile_id: int,
    score_key: str,
    behind_key: str,
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
    for position, (profile_id, score) in enumerate(ordered, start=1):
        if score != previous_score:
            rank = position
            previous_score = score
        all_rows.append(
            {
                "rank": rank,
                "display_name": public_woodchuck_name(profiles.get(profile_id)),
                score_key: score,
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
    leader_score = int(all_rows[0][score_key]) if all_rows else 0
    current_score = int(current_row[score_key]) if current_row else 0
    current_rank = int(current_row["rank"]) if current_row else None
    tied = bool(
        current_row
        and sum(
            row[score_key] == current_row[score_key]
            for row in all_rows
        ) > 1
    )
    in_top_five = current_index is not None and current_index < 5
    visible_rows = all_rows[:5]
    if current_row is not None and not in_top_five:
        visible_rows.append(current_row)

    return visible_rows, {
        "rank": current_rank,
        score_key: current_score,
        behind_key: max(leader_score - current_score, 0),
        "tied": tied,
        "in_top_five": in_top_five,
        "has_score": current_row is not None,
    }


def student_points_rows(
    scores: dict[int, int],
    profiles: dict[int, WoodchuckProfile],
    *,
    current_profile_id: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    return student_score_rows(
        scores,
        profiles,
        current_profile_id=current_profile_id,
        score_key="total_points",
        behind_key="points_behind_leader",
    )


def student_minutes_rows(
    scores: dict[int, int],
    profiles: dict[int, WoodchuckProfile],
    *,
    current_profile_id: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    return student_score_rows(
        scores,
        profiles,
        current_profile_id=current_profile_id,
        score_key="total_minutes",
        behind_key="minutes_behind_leader",
    )


def weekly_student_points(
    session: Session,
    *,
    contest_week: ContestWeek,
    current_profile_id: int,
) -> dict[str, object]:
    charts, approved_chart_ids = _charts_and_approved_ids(session, contest_week)

    open_scores: dict[int, int] = {}
    verified_scores: dict[int, int] = {}
    for chart in charts:
        open_scores[chart.profile_id] = (
            open_scores.get(chart.profile_id, 0) + chart.minutes
        )
        if chart.id in approved_chart_ids:
            verified_scores[chart.profile_id] = (
                verified_scores.get(chart.profile_id, 0) + chart.minutes
            )

    profile_ids = set(open_scores) | set(verified_scores)
    profiles = {
        profile.id: profile
        for profile in session.scalars(
            select(WoodchuckProfile).where(WoodchuckProfile.id.in_(profile_ids))
        ).all()
    } if profile_ids else {}
    open_rows, open_position = student_minutes_rows(
        open_scores,
        profiles,
        current_profile_id=current_profile_id,
    )
    verified_rows, verified_position = student_minutes_rows(
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


def _week_utc_bounds(contest_week: ContestWeek) -> tuple[datetime, datetime]:
    return (
        datetime.combine(contest_week.week_start, time.min, CENTRAL).astimezone(timezone.utc),
        datetime.combine(contest_week.week_end, time.min, CENTRAL).astimezone(timezone.utc),
    )


def weekly_camp_points(
    session: Session,
    *,
    contest_week: ContestWeek,
    current_profile_id: int,
) -> dict[str, object]:
    start_at, end_at = _week_utc_bounds(contest_week)
    awards = session.scalars(
        select(CampPointAward).where(
            CampPointAward.occurred_at >= start_at,
            CampPointAward.occurred_at < end_at,
        )
    ).all()
    scores: dict[int, int] = {}
    for award in awards:
        scores[award.profile_id] = scores.get(award.profile_id, 0) + award.points_awarded
    profiles = {
        profile.id: profile
        for profile in session.scalars(
            select(WoodchuckProfile).where(WoodchuckProfile.id.in_(scores))
        ).all()
    } if scores else {}
    rows, position = student_points_rows(
        scores, profiles, current_profile_id=current_profile_id
    )
    return {
        "open": rows,
        "current_user_position": {"open": position},
    }


def student_camp_point_totals(
    session: Session,
    *,
    profile_id: int,
    now: datetime,
) -> dict[str, int]:
    """Return persisted current-week and career Camp Point totals."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The current time must be timezone-aware.")
    central_now = now.astimezone(CENTRAL)
    monday = central_now.date() - timedelta(days=central_now.weekday())
    week_start = datetime.combine(
        monday, time.min, CENTRAL
    ).astimezone(timezone.utc)
    awards = session.scalars(select(CampPointAward).where(
        CampPointAward.profile_id == profile_id,
        CampPointAward.occurred_at <= now.astimezone(timezone.utc),
    )).all()
    return {
        "weekly_points": sum(
            award.points_awarded for award in awards
            if aware_utc(award.occurred_at) >= week_start
        ),
        "career_points": sum(award.points_awarded for award in awards),
    }


def create_camp_point_award(
    session: Session,
    *,
    profile: WoodchuckProfile,
    activity_type: str,
    activity_date: date,
    now: datetime,
) -> tuple[CampPointAward, bool]:
    activity = activity_type.strip().casefold()
    if activity not in CAMP_POINT_ACTIVITIES:
        raise ValueError("Unsupported Band Camp point activity.")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The award time must be timezone-aware.")
    if activity_date != now.astimezone(CENTRAL).date():
        raise ValueError("Band Camp activities can only be recorded for today.")
    duplicate_key = f"band-camp:{activity_date.isoformat()}:{activity}"
    existing = session.scalar(select(CampPointAward).where(
        CampPointAward.profile_id == profile.id,
        CampPointAward.duplicate_key == duplicate_key,
    ))
    if existing is not None:
        return existing, False
    award = CampPointAward(
        profile_id=profile.id,
        activity_type=activity,
        points_awarded=1,
        occurred_at=now.astimezone(timezone.utc),
        duplicate_key=duplicate_key,
    )
    session.add(award)
    session.flush()
    return award, True


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def medal_for_rank(rank: int) -> str | None:
    return {1: "gold", 2: "silver", 3: "bronze"}.get(rank)


def _charts_and_approved_ids(
    session: Session, contest_week: ContestWeek
) -> tuple[list[PracticeChart], set[int]]:
    charts = list(
        session.scalars(
            select(PracticeChart).where(
                PracticeChart.practice_date >= contest_week.week_start,
                PracticeChart.practice_date < contest_week.week_end,
                PracticeChart.include_contests.is_(True),
            )
        ).all()
    )
    chart_ids = [chart.id for chart in charts]
    approved = (
        set(
            session.scalars(
                select(PracticeChartVerification.practice_chart_id).where(
                    PracticeChartVerification.practice_chart_id.in_(chart_ids),
                    PracticeChartVerification.status == "approved",
                )
            ).all()
        )
        if chart_ids
        else set()
    )
    return charts, approved


def _ranked_student_scores(
    scores: dict[int, int], profiles: dict[int, WoodchuckProfile]
) -> list[tuple[int, str, int, int]]:
    ordered = sorted(scores.items(), key=lambda item: (
        -item[1],
        public_woodchuck_name(profiles.get(item[0])).casefold(),
        public_woodchuck_name(profiles.get(item[0])),
    ))
    rows: list[tuple[int, str, int, int]] = []
    previous: int | None = None
    rank = 0
    for position, (profile_id, score) in enumerate(ordered, start=1):
        if score != previous:
            rank, previous = position, score
        rows.append(
            (
                profile_id,
                public_woodchuck_name(profiles.get(profile_id)),
                score,
                rank,
            )
        )
    return rows


def _add_dandelion(session: Session, profile_id: int) -> None:
    state = session.get(WoodchuckState, profile_id)
    if state is None:
        state = WoodchuckState(profile_id=profile_id, state_json={}, revision=0)
        session.add(state)
    payload = deepcopy(state.state_json or {})
    progress = dict(payload.get("progress") or {})
    credits = progress.get("credits", 0)
    current_credits = (
        credits
        if isinstance(credits, int) and not isinstance(credits, bool)
        else 0
    )
    progress["credits"] = current_credits + 1
    payload["progress"] = progress
    state.state_json = payload
    state.revision += 1


def _grant_once(
    session: Session,
    *,
    profile_id: int,
    result_id: int | None,
    source_key: str,
    reward_type: str,
    category_key: str | None = None,
) -> bool:
    existing = session.scalar(select(RewardGrant.id).where(
        RewardGrant.profile_id == profile_id,
        RewardGrant.source_key == source_key,
        RewardGrant.reward_type == reward_type,
    ))
    if existing is not None:
        return False
    session.add(RewardGrant(
        profile_id=profile_id, contest_result_id=result_id,
        source_key=source_key, reward_type=reward_type,
        category_key=category_key, amount=1,
    ))
    return True


def finalize_contest_week(
    session: Session, *, week_start: date, now: datetime
) -> ContestWeek:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The current time must be timezone-aware.")
    contest_week = session.scalar(
        select(ContestWeek)
        .join(Season, Season.id == ContestWeek.season_id)
        .where(
            ContestWeek.week_start == week_start,
            Season.key.like("band-camp-%"),
        )
        .with_for_update()
    )
    if contest_week is None:
        raise HTTPException(status_code=404, detail="Contest week not found.")
    if contest_week.status == "finalized":
        return contest_week

    now_utc = now.astimezone(timezone.utc)
    week_end_at = datetime.combine(
        contest_week.week_end, time.min, CENTRAL
    ).astimezone(timezone.utc)
    if now_utc < week_end_at:
        raise HTTPException(status_code=409, detail="Contest week has not ended.")
    if now_utc <= aware_utc(contest_week.verification_deadline_at):
        raise HTTPException(status_code=409, detail="Verification deadline has not passed.")
    if now_utc <= aware_utc(contest_week.finalize_after):
        raise HTTPException(status_code=409, detail="Finalization time has not passed.")

    contest_keys = [definition["key"] for definition in CONTEST_DEFINITIONS]
    contests = {
        contest.key: contest
        for contest in session.scalars(
            select(Contest).where(Contest.key.in_(contest_keys))
        ).all()
    }
    if set(contests) != {definition["key"] for definition in CONTEST_DEFINITIONS}:
        raise RuntimeError("Band Camp contest definitions are missing.")

    charts, approved_ids = _charts_and_approved_ids(session, contest_week)
    start_at, end_at = _week_utc_bounds(contest_week)
    camp_awards = session.scalars(select(CampPointAward).where(
        CampPointAward.occurred_at >= start_at,
        CampPointAward.occurred_at < end_at,
    )).all()
    profile_ids = {chart.profile_id for chart in charts} | {
        award.profile_id for award in camp_awards
    }
    profiles = (
        {
            profile.id: profile
            for profile in session.scalars(
                select(WoodchuckProfile).where(
                    WoodchuckProfile.id.in_(profile_ids)
                )
            ).all()
        }
        if profile_ids
        else {}
    )

    point_scores = {"open": {}, "verified": {}}
    camp_point_scores: dict[int, int] = {}
    instrument_totals = {"open": {}, "verified": {}}
    contributions: dict[tuple[str, str, int], int] = {}
    for chart in charts:
        divisions = ["open"] + (["verified"] if chart.id in approved_ids else [])
        key, display = normalize_instrument(chart.instrument)
        for division in divisions:
            scores = point_scores[division]
            scores[chart.profile_id] = scores.get(chart.profile_id, 0) + chart.minutes
            if key:
                totals = instrument_totals[division]
                prior = totals.get(key, (display, 0))
                totals[key] = (prior[0], prior[1] + chart.minutes)
                contribution_key = (division, key, chart.profile_id)
                contributions[contribution_key] = contributions.get(contribution_key, 0) + chart.minutes
    for award in camp_awards:
        camp_point_scores[award.profile_id] = (
            camp_point_scores.get(award.profile_id, 0) + award.points_awarded
        )

    points_contest = contests["weekly-points-leaders"]
    instrument_contest = contests["weekly-practice-by-instrument"]
    camp_points_contest = contests["weekly-camp-points"]
    gold_results: dict[tuple[int, str], ContestResult] = {}
    winning_instruments: set[tuple[str, str]] = set()
    for division in ("open", "verified"):
        ranked_students = _ranked_student_scores(point_scores[division], profiles)
        for profile_id, display, score, rank in ranked_students:
            medal = medal_for_rank(rank)
            if medal is None:
                continue
            result = ContestResult(
                contest_week_id=contest_week.id, contest_id=points_contest.id,
                division=division, subject_type="student", subject_key=str(profile_id),
                profile_id=profile_id, instrument=None, display_name_snapshot=display,
                score=score, rank=rank, medal=medal,
            )
            session.add(result)
            if rank == 1:
                gold_results[(profile_id, division)] = result
        for row in olympic_rankings(instrument_totals[division]):
            rank = int(row["rank"])
            medal = medal_for_rank(rank)
            if medal is None:
                continue
            instrument = str(row["instrument"])
            key, _ = normalize_instrument(instrument)
            session.add(ContestResult(
                contest_week_id=contest_week.id, contest_id=instrument_contest.id,
                division=division, subject_type="instrument", subject_key=key,
                profile_id=None, instrument=instrument, display_name_snapshot=instrument,
                score=int(row["total_minutes"]), rank=rank, medal=medal,
            ))
            if rank == 1:
                winning_instruments.add((division, key))
    camp_gold_results: list[tuple[int, ContestResult]] = []
    for profile_id, display, score, rank in _ranked_student_scores(
        camp_point_scores, profiles
    ):
        medal = medal_for_rank(rank)
        if medal is None:
            continue
        result = ContestResult(
            contest_week_id=contest_week.id,
            contest_id=camp_points_contest.id,
            division="open",
            subject_type="student",
            subject_key=str(profile_id),
            profile_id=profile_id,
            instrument=None,
            display_name_snapshot=display,
            score=score,
            rank=rank,
            medal=medal,
        )
        session.add(result)
        if rank == 1:
            camp_gold_results.append((profile_id, result))
    session.flush()

    rewarded_gold: set[int] = set()
    for (profile_id, _division), result in gold_results.items():
        if profile_id in rewarded_gold:
            continue
        rewarded_gold.add(profile_id)
        source = (
            f"contest:{contest_week.week_start}:{points_contest.key}:"
            f"student:{profile_id}:gold"
        )
        if _grant_once(
            session,
            profile_id=profile_id,
            result_id=result.id,
            source_key=source,
            reward_type="dandelion",
        ):
            _add_dandelion(session, profile_id)
        category = points_contest.crown_category or points_contest.key
        if _grant_once(
            session,
            profile_id=profile_id,
            result_id=result.id,
            source_key=source,
            reward_type="crown_win",
            category_key=category,
        ):
            progress = session.scalar(select(CrownProgress).where(
                CrownProgress.profile_id == profile_id,
                CrownProgress.category_key == category,
            ).with_for_update())
            if progress is None:
                progress = CrownProgress(
                    profile_id=profile_id,
                    category_key=category,
                    qualifying_wins=0,
                )
                session.add(progress)
            progress.qualifying_wins += 1
            if progress.qualifying_wins >= 10 and progress.crown_earned_at is None:
                progress.crown_earned_at = now_utc

    for profile_id, result in camp_gold_results:
        source = (
            f"contest:{contest_week.week_start}:{camp_points_contest.key}:"
            f"student:{profile_id}:gold"
        )
        if _grant_once(
            session,
            profile_id=profile_id,
            result_id=result.id,
            source_key=source,
            reward_type="dandelion",
        ):
            _add_dandelion(session, profile_id)
        if _grant_once(
            session,
            profile_id=profile_id,
            result_id=result.id,
            source_key=source,
            reward_type="crown_win",
            category_key="weekly-camp-points",
        ):
            progress = session.scalar(select(CrownProgress).where(
                CrownProgress.profile_id == profile_id,
                CrownProgress.category_key == "weekly-camp-points",
            ).with_for_update())
            if progress is None:
                progress = CrownProgress(
                    profile_id=profile_id,
                    category_key="weekly-camp-points",
                    qualifying_wins=0,
                )
                session.add(progress)
            progress.qualifying_wins += 1
            if progress.qualifying_wins >= 10 and progress.crown_earned_at is None:
                progress.crown_earned_at = now_utc

    participation_winners: set[tuple[str, int]] = set()
    for winning_division, instrument_key in winning_instruments:
        for (division, key, profile_id), minutes in contributions.items():
            if division != winning_division or key != instrument_key or minutes < 15:
                continue
            participation_winners.add((instrument_key, profile_id))
    for instrument_key, profile_id in participation_winners:
        source = (
            f"contest:{contest_week.week_start}:{instrument_contest.key}:"
            f"instrument:{instrument_key}:participant:{profile_id}"
        )
        if _grant_once(
            session,
            profile_id=profile_id,
            result_id=None,
            source_key=source,
            reward_type="dandelion",
        ):
            _add_dandelion(session, profile_id)

    contest_week.status = "finalized"
    contest_week.finalized_at = now_utc
    session.flush()
    return contest_week


def contest_results_payload(
    session: Session, contest_week: ContestWeek
) -> dict[str, object]:
    rows = session.execute(select(ContestResult, Contest).join(
        Contest, Contest.id == ContestResult.contest_id
    ).where(ContestResult.contest_week_id == contest_week.id).order_by(
        Contest.key, ContestResult.division, ContestResult.rank, ContestResult.display_name_snapshot
    )).all()
    return {
        "week": {
            "week_start": contest_week.week_start.isoformat(),
            "week_end": contest_week.week_end.isoformat(),
            "status": contest_week.status,
            "verification_deadline_at": utc_iso(contest_week.verification_deadline_at),
            "finalize_after": utc_iso(contest_week.finalize_after),
            "finalized_at": utc_iso(contest_week.finalized_at),
        },
        "results": [{
            "contest": {"key": contest.key, "name": contest.name},
            "division": result.division, "rank": result.rank, "medal": result.medal,
            ("display_name" if result.subject_type == "student" else "instrument"): result.display_name_snapshot,
            "score": result.score,
        } for result, contest in rows],
    }


def finalized_weeks_payload(session: Session) -> dict[str, object]:
    rows = session.execute(
        select(ContestWeek, Season)
        .join(Season, Season.id == ContestWeek.season_id)
        .where(
            ContestWeek.status == "finalized",
            Season.key.like("band-camp-%"),
        )
        .order_by(
            ContestWeek.week_start.desc(),
            ContestWeek.finalized_at.desc(),
        )
    ).all()
    return {
        "weeks": [
            {
                "season": {"key": season.key, "name": season.name},
                "week_start": contest_week.week_start.isoformat(),
                "week_end": contest_week.week_end.isoformat(),
                "finalized_at": utc_iso(contest_week.finalized_at),
            }
            for contest_week, season in rows
        ]
    }


def _empty_medal_counts() -> dict[str, int]:
    return {"gold": 0, "silver": 0, "bronze": 0, "total": 0}


def _increment_medal(counts: dict[str, int], medal: str) -> None:
    counts[medal] += 1
    counts["total"] += 1


def _champion_sort_key(champion: dict[str, object]) -> tuple[object, ...]:
    medals = champion["medals"]
    name = champion.get("display_name", champion.get("instrument_label", ""))
    return (
        -medals["gold"],
        -medals["silver"],
        -medals["bronze"],
        str(name).casefold(),
        str(name),
    )


def hall_of_champions_payload(session: Session) -> dict[str, object]:
    rows = session.scalars(
        select(ContestResult)
        .join(ContestWeek, ContestWeek.id == ContestResult.contest_week_id)
        .join(Season, Season.id == ContestWeek.season_id)
        .where(
            ContestWeek.status == "finalized",
            Season.key.like("band-camp-%"),
        )
        .order_by(ContestWeek.week_start.desc(), ContestResult.id.desc())
    ).all()

    students_by_profile: dict[int, dict[str, object]] = {}
    instruments_by_key: dict[str, dict[str, object]] = {}
    for result in rows:
        if result.subject_type == "student" and result.profile_id is not None:
            champion = students_by_profile.get(result.profile_id)
            if champion is None:
                champion = {
                    "display_name": result.display_name_snapshot,
                    "medals": _empty_medal_counts(),
                    "by_division": {
                        "open": _empty_medal_counts(),
                        "verified": _empty_medal_counts(),
                    },
                    "divisions": set(),
                }
                students_by_profile[result.profile_id] = champion
        elif result.subject_type == "instrument":
            champion = instruments_by_key.get(result.subject_key)
            if champion is None:
                label = result.instrument or result.display_name_snapshot
                definition = INSTRUMENTS_BY_LABEL.get(label.casefold())
                champion = {
                    "instrument_key": result.subject_key,
                    "instrument_label": label,
                    "instrument_icon": (
                        definition["fallback_symbol"] if definition else "🎵"
                    ),
                    "medals": _empty_medal_counts(),
                    "by_division": {
                        "open": _empty_medal_counts(),
                        "verified": _empty_medal_counts(),
                    },
                    "divisions": set(),
                }
                instruments_by_key[result.subject_key] = champion
        else:
            continue

        _increment_medal(champion["medals"], result.medal)
        _increment_medal(champion["by_division"][result.division], result.medal)
        champion["divisions"].add(result.division)

    points_contest = session.scalar(
        select(Contest).where(Contest.key == "weekly-points-leaders")
    )
    crown_category = (
        points_contest.crown_category or points_contest.key
        if points_contest is not None
        else "weekly-points-leaders"
    )
    profile_ids = list(students_by_profile)
    crown_by_profile = {
        progress.profile_id: progress
        for progress in session.scalars(
            select(CrownProgress).where(
                CrownProgress.profile_id.in_(profile_ids),
                CrownProgress.category_key == crown_category,
            )
        ).all()
    } if profile_ids else {}

    students: list[dict[str, object]] = []
    for profile_id, champion in students_by_profile.items():
        progress = crown_by_profile.get(profile_id)
        champion["divisions"] = sorted(champion["divisions"])
        champion["crown"] = {
            "qualifying_wins": progress.qualifying_wins if progress else 0,
            "target_wins": 10,
            "earned": bool(progress and progress.crown_earned_at is not None),
        }
        students.append(champion)

    instruments = list(instruments_by_key.values())
    for champion in instruments:
        champion["divisions"] = sorted(champion["divisions"])
    students.sort(key=_champion_sort_key)
    instruments.sort(key=_champion_sort_key)
    return {"students": students, "instruments": instruments}


def crown_progress_payload(
    session: Session, *, profile_id: int
) -> dict[str, object]:
    _reconcile_crown_categories(session, profile_id=profile_id)
    progress_rows = {
        row.category_key: row for row in session.scalars(select(CrownProgress).where(
            CrownProgress.profile_id == profile_id,
            CrownProgress.category_key.in_([key for key, _ in CROWN_CATEGORIES]),
        )).all()
    }
    progress = progress_rows.get("weekly-points-leaders")
    qualifying_wins = progress.qualifying_wins if progress else 0
    earned_at = progress.crown_earned_at if progress else None
    return {
        "qualifying_wins": qualifying_wins,
        "target_wins": 10,
        "remaining_wins": max(10 - qualifying_wins, 0),
        "earned": earned_at is not None,
        "earned_at": utc_iso(earned_at),
        "categories": [
            {
                "key": key, "name": name,
                "progress": progress_rows[key].qualifying_wins if key in progress_rows else 0,
                "target": 10,
                "earned": bool(key in progress_rows and progress_rows[key].crown_earned_at),
                "earned_at": utc_iso(progress_rows[key].crown_earned_at) if key in progress_rows else None,
            }
            for key, name in CROWN_CATEGORIES
        ],
    }


def _set_crown_progress_at_least(
    session: Session, *, profile_id: int, category_key: str,
    count: int, earned_at: datetime | None,
) -> CrownProgress | None:
    if count <= 0:
        return None
    progress = session.scalar(select(CrownProgress).where(
        CrownProgress.profile_id == profile_id,
        CrownProgress.category_key == category_key,
    ).with_for_update())
    if progress is None:
        progress = CrownProgress(
            profile_id=profile_id, category_key=category_key, qualifying_wins=0,
        )
        session.add(progress)
    progress.qualifying_wins = max(progress.qualifying_wins, count)
    if progress.qualifying_wins >= 10 and progress.crown_earned_at is None:
        progress.crown_earned_at = earned_at or datetime.now(timezone.utc)
    return progress


def _reconcile_crown_categories(session: Session, *, profile_id: int) -> None:
    for activity, category_key in ACTIVITY_CROWN_KEYS.items():
        awards = list(session.scalars(select(CampPointAward.occurred_at).where(
            CampPointAward.profile_id == profile_id,
            CampPointAward.activity_type == activity,
        ).order_by(CampPointAward.occurred_at)).all())
        _set_crown_progress_at_least(
            session, profile_id=profile_id, category_key=category_key,
            count=len(awards), earned_at=awards[9] if len(awards) >= 10 else None,
        )

    camp_gold_dates = list(session.scalars(
        select(ContestResult.created_at)
        .join(Contest, Contest.id == ContestResult.contest_id)
        .join(ContestWeek, ContestWeek.id == ContestResult.contest_week_id)
        .where(
            ContestResult.profile_id == profile_id,
            ContestResult.rank == 1,
            ContestResult.medal == "gold",
            Contest.key == "weekly-camp-points",
            ContestWeek.status == "finalized",
        )
        .order_by(ContestResult.created_at)
    ).all())
    _set_crown_progress_at_least(
        session, profile_id=profile_id, category_key="weekly-camp-points",
        count=len(camp_gold_dates),
        earned_at=camp_gold_dates[9] if len(camp_gold_dates) >= 10 else None,
    )
    session.flush()


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
    camp_points_standings = weekly_camp_points(
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
            "weekly-camp-points": camp_points_standings,
        },
    }


def quest_definition(instrument: str, quest_id: str) -> dict[str, object] | None:
    return next(
        (quest for quest in QUEST_POOL.get(instrument, ()) if quest["id"] == quest_id),
        None,
    )


def quest_completion_payload(
    session: Session,
    *,
    completion: QuestCompletion,
    created: bool,
    reward_created: bool,
    now: datetime,
) -> dict[str, object]:
    state = session.get(WoodchuckState, completion.profile_id)
    state_json = state.state_json if state is not None else {}
    progress = state_json.get("progress") if isinstance(state_json, dict) else {}
    credits = progress.get("credits", 0) if isinstance(progress, dict) else 0
    if not isinstance(credits, int) or isinstance(credits, bool):
        credits = 0
    streak = progress.get("streak", 0) if isinstance(progress, dict) else 0
    if not isinstance(streak, int) or isinstance(streak, bool):
        streak = 0
    return {
        "created": created,
        "reward_created": reward_created,
        "camp_point_created": False,
        "crown_newly_earned": False,
        "credits": credits,
        "streak": streak,
        "revision": state.revision if state is not None else 0,
        "completion": {
            "id": completion.id,
            "activity_date": completion.activity_date.isoformat(),
            "quest_id": completion.quest_id,
            "logged_minutes": completion.logged_minutes,
            "reward_amount": completion.reward_amount,
            "completed_at": utc_iso(completion.completed_at),
        },
        **student_camp_point_totals(
            session, profile_id=completion.profile_id, now=now
        ),
    }


@router.post("/quest/completions")
def complete_quest(
    request: Request,
    submitted: QuestCompletionSubmission,
) -> dict[str, object]:
    """Persist one daily Bonus Challenge and its configured reward atomically."""
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        now = datetime.now(timezone.utc)
        today = now.astimezone(CENTRAL).date()
        if submitted.activity_date != today:
            raise HTTPException(status_code=400, detail="Quests can only be completed for today.")
        definition = quest_definition(profile.instrument, submitted.quest_id)
        if definition is None:
            raise HTTPException(status_code=400, detail="Choose a current quest for your instrument.")
        target_minutes = int(definition["target_minutes"])
        reward_amount = int(definition["reward_credits"])
        if submitted.logged_minutes < target_minutes:
            raise HTTPException(
                status_code=400,
                detail=f"Log at least {target_minutes} minutes to complete this quest.",
            )

        existing = session.scalar(select(QuestCompletion).where(
            QuestCompletion.profile_id == profile.id,
            QuestCompletion.activity_date == today,
        ))
        if existing is not None:
            return quest_completion_payload(
                session, completion=existing, created=False,
                reward_created=False, now=now,
            )

        state = session.get(WoodchuckState, profile.id)
        completion = QuestCompletion(
            profile_id=profile.id,
            activity_date=today,
            quest_id=submitted.quest_id,
            logged_minutes=submitted.logged_minutes,
            reward_amount=reward_amount,
            completed_at=now,
        )
        session.add(completion)
        source_key = f"quest:{today.isoformat()}"
        session.add(RewardGrant(
            profile_id=profile.id,
            contest_result_id=None,
            source_key=source_key,
            reward_type="dandelion",
            category_key=None,
            amount=reward_amount,
        ))

        if state is None:
            state = WoodchuckState(profile_id=profile.id, state_json={}, revision=0)
            session.add(state)
        state_json = deepcopy(state.state_json or {})
        progress = dict(state_json.get("progress") or {})
        current_credits = progress.get("credits", 0)
        if not isinstance(current_credits, int) or isinstance(current_credits, bool):
            current_credits = 0
        progress["credits"] = current_credits + reward_amount
        last_date = progress.get("lastCompletedDate")
        yesterday = (today - timedelta(days=1)).isoformat()
        current_streak = progress.get("streak", 0)
        if not isinstance(current_streak, int) or isinstance(current_streak, bool):
            current_streak = 0
        if last_date != today.isoformat():
            progress["streak"] = current_streak + 1 if last_date == yesterday else 1
            progress["lastCompletedDate"] = today.isoformat()
        state_json["progress"] = progress

        completed_at = now.isoformat()
        daily = dict(state_json.get("daily") or {})
        daily.update({
            "dateKey": today.isoformat(),
            "questId": submitted.quest_id,
            "questText": definition["text"],
            "targetMinutes": target_minutes,
            "rewardCredits": reward_amount,
            "loggedMinutes": submitted.logged_minutes,
            "completed": True,
            "completedAt": completed_at,
        })
        state_json["daily"] = daily
        state_json["quest"] = {
            "dateKey": today.isoformat(),
            "text": definition["text"],
            "targetMinutes": target_minutes,
            "completed": True,
            "rewardCredits": reward_amount,
        }
        practice_log = list(state_json.get("practiceLog") or [])
        practice_log.insert(0, {
            "dateKey": today.isoformat(),
            "minutes": submitted.minutes,
            "note": submitted.note.strip(),
            "questId": submitted.quest_id,
            "creditsAwarded": reward_amount,
            "loggedAt": completed_at,
            "source": "quest",
        })
        state_json["practiceLog"] = practice_log[:50]
        state.revision += 1
        account = dict(state_json.get("account") or {})
        account["serverRevision"] = state.revision
        account["lastSyncedAt"] = completed_at
        state_json["account"] = account
        state.state_json = state_json

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(select(QuestCompletion).where(
                QuestCompletion.profile_id == profile.id,
                QuestCompletion.activity_date == today,
            ))
            if existing is None:
                raise HTTPException(status_code=500, detail="Quest completion could not be saved.")
            return quest_completion_payload(
                session, completion=existing, created=False,
                reward_created=False, now=now,
            )
        return quest_completion_payload(
            session, completion=completion, created=True,
            reward_created=True, now=now,
        )


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


@router.get("/camp-points/awards/{activity_date}")
def daily_camp_point_awards(
    activity_date: date,
    request: Request,
) -> dict[str, object]:
    """Return persisted completion records for one student's requested day."""
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        now = datetime.now(timezone.utc)
        prefix = f"band-camp:{activity_date.isoformat()}:"
        awards = session.scalars(select(CampPointAward).where(
            CampPointAward.profile_id == profile.id,
            CampPointAward.duplicate_key.like(f"{prefix}%"),
        )).all()
        trivia_attempt = session.scalar(select(DailyTriviaAttempt).where(
            DailyTriviaAttempt.profile_id == profile.id,
            DailyTriviaAttempt.activity_date == activity_date,
        ))
        trivia_attempt_payload = None
        if trivia_attempt is not None:
            selected_choice = trivia_selected_choice(
                activity_date, trivia_attempt.selected_answer,
                correct=trivia_attempt.correct,
            )
            trivia_attempt_payload = {
                "selected_answer_id": selected_choice["id"] if selected_choice else None,
                "correct": trivia_attempt.correct,
            }
        elif any(award.activity_type == "trivia" for award in awards):
            # Pre-attempt-ledger trivia awards prove the submitted choice was
            # correct; reconstruct that server-authored option for compatibility.
            question = trivia_question_for(activity_date)
            trivia_attempt_payload = {
                "selected_answer_id": question["correct_answer_id"],
                "correct": True,
            }
        return {
            "activity_date": activity_date.isoformat(),
            "trivia_question": public_trivia_question(activity_date),
            **student_camp_point_totals(
                session, profile_id=profile.id, now=now
            ),
            "awards": [
                {
                    "activity_type": award.activity_type,
                    "points_awarded": award.points_awarded,
                    "occurred_at": utc_iso(award.occurred_at),
                }
                for award in awards
            ],
            "trivia_attempt": trivia_attempt_payload,
        }


@router.post("/trivia/answer")
def check_trivia_answer(
    request: Request,
    submitted: TriviaAnswerSubmission,
) -> dict[str, object]:
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        now = datetime.now(timezone.utc)
        today = now.astimezone(CENTRAL).date()
        if submitted.activity_date != today:
            raise HTTPException(status_code=400, detail="Trivia can only be answered for today.")
        question = trivia_question_for(today)
        choice = next(
            (item for item in question["choices"] if item["id"] == submitted.selected_answer_id),
            None,
        )
        if choice is None:
            raise HTTPException(status_code=400, detail="Choose one of today’s answers.")
        attempt = session.scalar(select(DailyTriviaAttempt).where(
            DailyTriviaAttempt.profile_id == profile.id,
            DailyTriviaAttempt.activity_date == today,
        ))
        created = attempt is None
        if attempt is None:
            attempt = DailyTriviaAttempt(
                profile_id=profile.id,
                activity_date=today,
                selected_answer=choice["id"],
                correct=choice["id"] == question["correct_answer_id"],
            )
            session.add(attempt)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                attempt = session.scalar(select(DailyTriviaAttempt).where(
                    DailyTriviaAttempt.profile_id == profile.id,
                    DailyTriviaAttempt.activity_date == today,
                ))
                if attempt is None:
                    raise HTTPException(status_code=500, detail="Trivia could not be saved.")
                created = False
        award = None
        award_created = False
        if attempt.correct:
            award, award_created = create_camp_point_award(
                session,
                profile=profile,
                activity_type="trivia",
                activity_date=today,
                now=now,
            )
            _reconcile_crown_categories(session, profile_id=profile.id)
        session.commit()
        return {
            "question": question["question"],
            "selected_answer_id": (
                (trivia_selected_choice(
                    today, attempt.selected_answer, correct=attempt.correct
                ) or {}).get("id")
            ),
            "correct": attempt.correct,
            "created": created,
            "award_created": award_created,
            "award": ({
                "activity_type": award.activity_type,
                "points_awarded": award.points_awarded,
                "occurred_at": utc_iso(award.occurred_at),
            } if award is not None else None),
            **student_camp_point_totals(session, profile_id=profile.id, now=now),
        }


@router.post("/camp-points/awards")
def award_camp_points(
    request: Request,
    submitted: CampPointAwardCreate,
) -> dict[str, object]:
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        now = datetime.now(timezone.utc)
        try:
            award, created = create_camp_point_award(
                session,
                profile=profile,
                activity_type=submitted.activity_type,
                activity_date=submitted.activity_date,
                now=now,
            )
            _reconcile_crown_categories(session, profile_id=profile.id)
            session.commit()
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except IntegrityError:
            session.rollback()
            duplicate_key = (
                f"band-camp:{submitted.activity_date.isoformat()}:"
                f"{submitted.activity_type.strip().casefold()}"
            )
            award = session.scalar(select(CampPointAward).where(
                CampPointAward.profile_id == profile.id,
                CampPointAward.duplicate_key == duplicate_key,
            ))
            if award is None:
                raise HTTPException(status_code=500, detail="Camp points could not be saved.")
            created = False
        return {
            "created": created,
            **student_camp_point_totals(
                session, profile_id=profile.id, now=now
            ),
            "award": {
                "activity_type": award.activity_type,
                "points_awarded": award.points_awarded,
                "occurred_at": utc_iso(award.occurred_at),
            },
        }


@router.post("/weeks/{week_start}/finalize")
def finalize_week_route(
    week_start: date,
    request: Request,
) -> dict[str, object]:
    configured_token = os.getenv("CONTEST_ADMIN_TOKEN")
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="Contest finalization is unavailable.",
        )
    supplied_token = request.headers.get("X-Contest-Admin-Token", "")
    if not hmac.compare_digest(supplied_token, configured_token):
        raise HTTPException(status_code=403, detail="Invalid contest admin token.")
    with SessionLocal() as session:
        with session.begin():
            contest_week = finalize_contest_week(
                session, week_start=week_start, now=datetime.now(timezone.utc)
            )
        return contest_results_payload(session, contest_week)


@router.get("/weeks/finalized")
def finalized_contest_weeks(request: Request) -> dict[str, object]:
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        return finalized_weeks_payload(session)


@router.get("/hall-of-champions")
def hall_of_champions(request: Request) -> dict[str, object]:
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        return hall_of_champions_payload(session)


@router.get("/crown-progress")
def current_crown_progress(request: Request) -> dict[str, object]:
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        payload = crown_progress_payload(session, profile_id=profile.id)
        session.commit()
        return payload


@router.get("/seasons/status")
def contest_season_status(request: Request) -> dict[str, object]:
    from .contest_seasons import season_status_payload

    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        return season_status_payload(session, now=datetime.now(timezone.utc))


@router.get("/weeks/{week_start}/results")
def contest_week_results(week_start: date, request: Request) -> dict[str, object]:
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        contest_week = session.scalar(
            select(ContestWeek)
            .join(Season, Season.id == ContestWeek.season_id)
            .where(
                ContestWeek.week_start == week_start,
                Season.key.like("band-camp-%"),
                ContestWeek.status == "finalized",
            )
            .order_by(Season.starts_on.desc())
        )
        if contest_week is None:
            raise HTTPException(status_code=404, detail="Contest week not found.")
        return contest_results_payload(session, contest_week)
