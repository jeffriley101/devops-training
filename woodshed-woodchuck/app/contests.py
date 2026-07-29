from __future__ import annotations

import hmac
import os
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_routes import current_profile
from .db import SessionLocal
from .instruments import INSTRUMENTS_BY_LABEL
from .models import (
    CampPointAward,
    Contest,
    ContestResult,
    ContestWeek,
    CrownProgress,
    PracticeChart,
    PracticeChartVerification,
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

router = APIRouter(prefix="/contests", tags=["contests"])


class CampPointAwardCreate(BaseModel):
    activity_type: str
    activity_date: date


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
                crown_category=definition["crown_category"],
                active=True,
            )
            session.add(contest)
        else:
            contest.name = definition["name"]
            contest.metric_type = definition["metric_type"]
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
            Season.key == BAND_CAMP_KEY,
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
    points_contest = session.scalar(
        select(Contest).where(Contest.key == "weekly-points-leaders")
    )
    crown_category = (
        points_contest.crown_category or points_contest.key
        if points_contest is not None
        else "weekly-points-leaders"
    )
    progress = session.scalar(
        select(CrownProgress).where(
            CrownProgress.profile_id == profile_id,
            CrownProgress.category_key == crown_category,
        )
    )
    qualifying_wins = progress.qualifying_wins if progress is not None else 0
    earned_at = progress.crown_earned_at if progress is not None else None
    return {
        "qualifying_wins": qualifying_wins,
        "target_wins": 10,
        "remaining_wins": max(10 - qualifying_wins, 0),
        "earned": earned_at is not None,
        "earned_at": utc_iso(earned_at),
    }


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
        return crown_progress_payload(session, profile_id=profile.id)


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
