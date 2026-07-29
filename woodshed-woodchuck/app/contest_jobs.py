from __future__ import annotations

import json
import argparse
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from typing import Callable, Sequence, TextIO

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .contests import (
    CENTRAL,
    aware_utc,
    finalize_contest_week,
)
from .db import SessionLocal
from .contest_seasons import rollover_season
from .models import ContestWeek, Season


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


EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")


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


def _candidate_reason(candidate: WeekCandidate, now: datetime) -> str | None:
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
            .where(Season.key.like("band-camp-%"))
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
    run_now = now or datetime.now(timezone.utc)
    if run_now.tzinfo is None or run_now.utcoffset() is None:
        raise ValueError("The job time must be timezone-aware.")

    _log(stream, "run_started", job="finalize_due_weeks", at=run_now.isoformat())
    candidates = _load_candidates(session_factory)
    due = [candidate for candidate in candidates if _candidate_reason(candidate, run_now) is None]
    _log(stream, "due_weeks_found", count=len(due))

    skipped = 0
    for candidate in candidates:
        reason = _candidate_reason(candidate, run_now)
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
        try:
            with session_factory() as session:
                with session.begin():
                    finalized_week = finalizer(
                        session,
                        week_start=candidate.week_start,
                        now=run_now,
                    )
                if finalized_week.finalized_at is None:
                    raise RuntimeError("Finalization completed without a timestamp.")
            finalized += 1
            _log(stream, "week_finalized", week_start=week_text)
        except Exception as error:  # continue safely to later independent weeks
            failed += 1
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
    return (1 if failed else 0), summary


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["finalize_due_weeks"]:
        exit_code, _summary = run_finalize_due_weeks()
        return exit_code
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
            "{finalize_due_weeks|rollover_season ...}\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
