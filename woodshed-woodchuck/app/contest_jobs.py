from __future__ import annotations

import json
import argparse
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from typing import Callable, Sequence, TextIO

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .contests import (
    CENTRAL,
    aware_utc,
    contest_season_clause,
    finalize_contest_week,
)
from .db import SessionLocal
from .contest_seasons import rollover_season
from .models import (
    CampPointAward,
    ContestResult,
    ContestWeek,
    CrownProgress,
    PracticeChart,
    RewardGrant,
    Season,
    TeamWeekMembershipSnapshot,
    WoodchuckState,
)


@dataclass(frozen=True)
class JobSummary:
    due: int = 0
    finalized: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(frozen=True)
class WeekCandidate:
    week_start: date
    week_end: date
    status: str
    verification_deadline_at: datetime
    finalize_after: datetime


def _credits_total(session: Session) -> int:
    total = 0
    for payload in session.scalars(select(WoodchuckState.state_json)).all():
        progress = payload.get("progress") if isinstance(payload, dict) else None
        credits = progress.get("credits") if isinstance(progress, dict) else None
        if isinstance(credits, int) and not isinstance(credits, bool):
            total += credits
    return total


def _history_counts(session: Session, week: ContestWeek) -> dict[str, int]:
    result_ids = select(ContestResult.id).where(
        ContestResult.contest_week_id == week.id
    )
    return {
        "source_charts": session.scalar(select(func.count()).select_from(
            PracticeChart
        ).where(
            PracticeChart.practice_date >= week.week_start,
            PracticeChart.practice_date < week.week_end,
            PracticeChart.include_contests.is_(True),
        )) or 0,
        "results": session.scalar(select(func.count()).select_from(
            ContestResult
        ).where(ContestResult.contest_week_id == week.id)) or 0,
        "medals": session.scalar(select(func.count()).select_from(
            ContestResult
        ).where(ContestResult.contest_week_id == week.id)) or 0,
        "hall_rows": session.scalar(select(func.count()).select_from(
            ContestResult
        ).where(
            ContestResult.contest_week_id == week.id,
            ContestResult.subject_type.in_(("student", "instrument")),
        )) or 0,
        "reward_grants": session.scalar(select(func.count()).select_from(
            RewardGrant
        ).where(
            (RewardGrant.source_key.like(f"contest:{week.id}:%"))
            | RewardGrant.contest_result_id.in_(result_ids)
        )) or 0,
        "camp_point_awards": session.scalar(select(func.count()).select_from(
            CampPointAward
        ).where(CampPointAward.duplicate_key.like(f"contest:{week.id}:%"))) or 0,
        "membership_snapshots": session.scalar(select(func.count()).select_from(
            TeamWeekMembershipSnapshot
        ).where(TeamWeekMembershipSnapshot.contest_week_id == week.id)) or 0,
        "crown_progress": session.scalar(select(func.coalesce(
            func.sum(CrownProgress.qualifying_wins), 0
        ))) or 0,
        "dandelions": _credits_total(session),
    }


def audit_or_repair_history(
    session: Session, *, week_start: date, now: datetime, apply: bool = False
) -> dict[str, object]:
    """Diagnose one week and optionally fill only deterministic missing artifacts.

    Dry-run is the default and rolls back its savepoint. Open weeks are processed
    only after their preserved verification and finalization deadlines.
    """
    week = session.scalar(select(ContestWeek).join(Season).where(
        ContestWeek.week_start == week_start,
        contest_season_clause(),
    ))
    if week is None:
        raise ValueError("Contest week not found.")
    reason = candidate_reason(WeekCandidate(
        week_start=week.week_start,
        week_end=week.week_end,
        status=week.status,
        verification_deadline_at=week.verification_deadline_at,
        finalize_after=week.finalize_after,
    ), now)
    before = _history_counts(session, week)
    if week.status != "finalized" and reason is not None:
        return {
            "week_start": week.week_start.isoformat(), "status": week.status,
            "mode": "apply" if apply else "dry_run", "action": "not_due",
            "reason": reason, "before": before, "after": before,
            "created": {key: 0 for key in before if key != "source_charts"},
        }

    savepoint = session.begin_nested() if not apply else None
    finalize_contest_week(
        session, week_start=week.week_start, now=now,
        repair_finalized=week.status == "finalized",
    )
    session.flush()
    after = _history_counts(session, week)
    created = {
        key: after[key] - before[key]
        for key in before if key != "source_charts"
    }
    action = "repaired" if any(value for value in created.values()) else "unchanged"
    if not apply:
        assert savepoint is not None
        savepoint.rollback()
        session.expire_all()
    return {
        "week_start": week.week_start.isoformat(), "status": week.status,
        "mode": "apply" if apply else "dry_run", "action": action,
        "reason": None, "before": before, "after": after, "created": created,
    }


EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_latest_finalize_outcome: dict[str, object] | None = None


def latest_finalize_outcome() -> dict[str, object] | None:
    return dict(_latest_finalize_outcome) if _latest_finalize_outcome else None


def _log(stream: TextIO, event: str, **fields: object) -> None:
    stream.write(json.dumps({"event": event, **fields}, sort_keys=True) + "\n")
    stream.flush()


def _safe_error_message(error: Exception) -> str:
    if isinstance(error, HTTPException):
        message = str(error.detail)
    else:
        message = "Unexpected finalization failure."
    message = EMAIL_PATTERN.sub("[redacted-email]", message)
    message = re.sub(
        r"(?i)\b(pin|password|token|database_url)\s*[=:]\s*\S+",
        r"\1=[redacted]",
        message,
    )
    return message[:300]


SAFE_DIAGNOSTIC_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
FINALIZATION_STAGES = {
    "membership_snapshots",
    "contest_results",
    "rewards",
    "camp_points",
    "crown_progress",
    "final_week_status_flush_commit",
}


def _safe_diagnostic_value(value: object) -> str | None:
    if not isinstance(value, str) or not SAFE_DIAGNOSTIC_VALUE.fullmatch(value):
        return None
    return value


def _integrity_error_fields(error: IntegrityError) -> dict[str, str]:
    original = error.orig
    diagnostic = getattr(original, "diag", None)
    fields: dict[str, str] = {}
    sqlstate = _safe_diagnostic_value(
        getattr(original, "sqlstate", None)
        or getattr(original, "pgcode", None)
        or getattr(diagnostic, "sqlstate", None)
    )
    if sqlstate is not None:
        fields["sqlstate"] = sqlstate
    for field, attribute in (
        ("constraint", "constraint_name"),
        ("table", "table_name"),
        ("column", "column_name"),
    ):
        value = _safe_diagnostic_value(getattr(diagnostic, attribute, None))
        if value is not None:
            fields[field] = value
    return fields


def candidate_reason(candidate: WeekCandidate, now: datetime) -> str | None:
    if candidate.status == "finalized":
        return "already_finalized"
    if candidate.status != "open":
        return "not_open"
    now_utc = now.astimezone(timezone.utc)
    week_end_at = datetime.combine(candidate.week_end, time.min, CENTRAL).astimezone(
        timezone.utc
    )
    if now_utc < week_end_at:
        return "week_not_ended"
    if now_utc <= aware_utc(candidate.verification_deadline_at):
        return "verification_deadline_not_passed"
    if now_utc <= aware_utc(candidate.finalize_after):
        return "finalize_after_not_passed"
    return None


def _load_candidates(factory: sessionmaker[Session]) -> list[WeekCandidate]:
    with factory() as session:
        rows = session.scalars(
            select(ContestWeek)
            .join(Season, Season.id == ContestWeek.season_id)
            .where(contest_season_clause())
            .order_by(ContestWeek.week_start)
        ).all()
        return [
            WeekCandidate(
                week_start=row.week_start,
                week_end=row.week_end,
                status=row.status,
                verification_deadline_at=row.verification_deadline_at,
                finalize_after=row.finalize_after,
            )
            for row in rows
        ]


def run_finalize_due_weeks(
    *,
    now: datetime | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
    stream: TextIO = sys.stdout,
    finalizer: Callable[..., ContestWeek] = finalize_contest_week,
) -> tuple[int, JobSummary]:
    global _latest_finalize_outcome
    run_now = now or datetime.now(timezone.utc)
    if run_now.tzinfo is None or run_now.utcoffset() is None:
        raise ValueError("The job time must be timezone-aware.")

    _log(stream, "run_started", job="finalize_due_weeks", at=run_now.isoformat())
    candidates = _load_candidates(session_factory)
    due = [candidate for candidate in candidates if candidate_reason(candidate, run_now) is None]
    _log(stream, "due_weeks_found", count=len(due))

    skipped = 0
    for candidate in candidates:
        reason = candidate_reason(candidate, run_now)
        if reason is None:
            continue
        skipped += 1
        _log(
            stream,
            "week_skipped",
            week_start=candidate.week_start.isoformat(),
            reason=reason,
        )

    finalized = 0
    failed = 0
    for candidate in due:
        week_text = candidate.week_start.isoformat()
        _log(stream, "week_finalizing", week_start=week_text)
        transaction_session: Session | None = None
        try:
            with session_factory() as transaction_session:
                with transaction_session.begin():
                    finalized_week = finalizer(
                        transaction_session,
                        week_start=candidate.week_start,
                        now=run_now,
                    )
                if finalized_week.finalized_at is None:
                    raise RuntimeError("Finalization completed without a timestamp.")
            finalized += 1
            _log(stream, "week_finalized", week_start=week_text)
        except Exception as error:  # continue safely to later independent weeks
            failed += 1
            if isinstance(error, IntegrityError):
                stage = "unknown"
                if transaction_session is not None:
                    reported_stage = transaction_session.info.get(
                        "contest_finalization_stage"
                    )
                    if reported_stage in FINALIZATION_STAGES:
                        stage = str(reported_stage)
                _log(
                    stream,
                    "week_integrity_error",
                    week_start=week_text,
                    stage=stage,
                    **_integrity_error_fields(error),
                )
            _log(
                stream,
                "week_failed",
                week_start=week_text,
                exception_type=type(error).__name__,
                message=_safe_error_message(error),
            )

    summary = JobSummary(
        due=len(due), finalized=finalized, skipped=skipped, failed=failed
    )
    _log(stream, "run_finished", **asdict(summary))
    _latest_finalize_outcome = {
        "at": run_now.isoformat(),
        "exit_code": 1 if failed else 0,
        **asdict(summary),
    }
    return (1 if failed else 0), summary


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["finalize_due_weeks"]:
        exit_code, _summary = run_finalize_due_weeks()
        return exit_code
    if arguments and arguments[0] == "audit_history":
        parser = argparse.ArgumentParser(
            prog="python -m app.contest_jobs audit_history"
        )
        parser.add_argument("--week", required=True, type=date.fromisoformat)
        parser.add_argument("--apply", action="store_true")
        try:
            submitted = parser.parse_args(arguments[1:])
        except SystemExit as error:
            return int(error.code)
        try:
            with SessionLocal() as session:
                report = audit_or_repair_history(
                    session, week_start=submitted.week,
                    now=datetime.now(timezone.utc), apply=submitted.apply,
                )
                if submitted.apply:
                    session.commit()
            _log(sys.stdout, "contest_history_audit", **report)
            return 0
        except Exception as error:
            _log(
                sys.stdout, "contest_history_audit_failed",
                week_start=submitted.week.isoformat(),
                exception_type=type(error).__name__,
                message=_safe_error_message(error),
            )
            return 1
    if arguments and arguments[0] == "rollover_season":
        parser = argparse.ArgumentParser(
            prog="python -m app.contest_jobs rollover_season"
        )
        parser.add_argument("--source-key", required=True)
        parser.add_argument("--next-key", required=True)
        parser.add_argument("--next-name", required=True)
        parser.add_argument("--start", required=True, type=date.fromisoformat)
        parser.add_argument("--end", required=True, type=date.fromisoformat)
        try:
            submitted = parser.parse_args(arguments[1:])
        except SystemExit as error:
            return int(error.code)
        now = datetime.now(timezone.utc)
        _log(sys.stdout, "run_started", job="rollover_season", at=now.isoformat())
        try:
            with SessionLocal() as session:
                with session.begin():
                    result = rollover_season(
                        session,
                        source_key=submitted.source_key,
                        next_key=submitted.next_key,
                        next_name=submitted.next_name,
                        next_starts_on=submitted.start,
                        next_ends_on=submitted.end,
                        now=now,
                    )
            _log(
                sys.stdout,
                "season_rollover_finished",
                source_key=result.source_key,
                next_key=result.next_key,
                weeks=result.weeks_created,
                created=result.created,
            )
            return 0
        except Exception as error:
            _log(
                sys.stdout,
                "season_rollover_failed",
                source_key=submitted.source_key,
                next_key=submitted.next_key,
                exception_type=type(error).__name__,
                message=_safe_error_message(error),
            )
            return 1
    else:
        sys.stderr.write(
            "Usage: python -m app.contest_jobs "
            "{finalize_due_weeks|audit_history --week YYYY-MM-DD [--apply]|"
            "rollover_season ...}\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
