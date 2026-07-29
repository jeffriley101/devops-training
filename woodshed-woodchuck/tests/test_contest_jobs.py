from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import contest_jobs
from app.contest_jobs import JobSummary, run_finalize_due_weeks
from app.contests import ensure_band_camp_data, finalize_contest_week
from app.db import Base
from app.models import (
    CampPointAward,
    Contest,
    ContestResult,
    ContestWeek,
    PracticeChart,
    PracticeChartVerification,
    RewardGrant,
    Season,
    WoodchuckProfile,
)


NOW = datetime(2026, 8, 3, 18, tzinfo=timezone.utc)


@pytest.fixture
def database() -> tuple[Session, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session, factory


def logs(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_command_interface_and_usage_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        contest_jobs,
        "run_finalize_due_weeks",
        lambda: (0, JobSummary()),
    )

    assert contest_jobs.main(["finalize_due_weeks"]) == 0
    assert contest_jobs.main([]) == 2
    assert "python -m app.contest_jobs finalize_due_weeks" in capsys.readouterr().err


def add_week(
    session: Session,
    season: Season,
    *,
    week_start: date,
    status: str = "open",
    deadline: datetime | None = None,
    finalize_after: datetime | None = None,
) -> ContestWeek:
    week_end = week_start + timedelta(days=7)
    week = ContestWeek(
        season_id=season.id,
        week_start=week_start,
        week_end=week_end,
        verification_deadline_at=deadline or NOW - timedelta(hours=1),
        finalize_after=finalize_after or NOW - timedelta(minutes=30),
        status=status,
        finalized_at=NOW - timedelta(days=1) if status == "finalized" else None,
    )
    session.add(week)
    session.commit()
    return week


def add_student(session: Session, suffix: str) -> WoodchuckProfile:
    profile = WoodchuckProfile(
        woodchuck_id=f"WC-JOB-{suffix}",
        display_name=f"Job Student {suffix}",
        pin_hash="private-hash",
        instrument="Clarinet",
        level="Beginner",
        goal="Practice",
    )
    session.add(profile)
    session.flush()
    return profile


def add_week_activity(
    session: Session,
    profile: WoodchuckProfile,
    week: ContestWeek,
) -> None:
    chart = PracticeChart(
        profile_id=profile.id,
        practice_date=week.week_start,
        minutes=25,
        instrument=profile.instrument,
        practice_details=[],
        source="p-book",
        credits_awarded=0,
    )
    session.add(chart)
    session.flush()
    session.add(PracticeChartVerification(
        practice_chart_id=chart.id,
        verifier_id=None,
        status="approved",
    ))
    session.add(CampPointAward(
        profile_id=profile.id,
        activity_type="care",
        points_awarded=1,
        occurred_at=datetime.combine(
            week.week_start, datetime.min.time(), timezone.utc
        ) + timedelta(hours=18),
        duplicate_key=f"job:{week.week_start}:care",
    ))
    session.commit()


def test_no_due_weeks_exits_successfully(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, factory = database
    ensure_band_camp_data(session, now=datetime(2026, 7, 28, tzinfo=timezone.utc))
    output = io.StringIO()

    code, summary = run_finalize_due_weeks(
        now=datetime(2026, 7, 28, tzinfo=timezone.utc),
        session_factory=factory,
        stream=output,
    )

    assert code == 0
    assert summary.due == summary.finalized == summary.failed == 0
    assert summary.skipped == 1
    records = logs(output)
    assert records[0]["event"] == "run_started"
    assert any(row["event"] == "due_weeks_found" and row["count"] == 0 for row in records)
    assert records[-1] == {
        "event": "run_finished", "due": 0, "finalized": 0,
        "skipped": 1, "failed": 0,
    }


def test_one_due_week_finalizes_and_repeated_run_is_idempotent(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, factory = database
    _, _, week = ensure_band_camp_data(session, now=datetime(2026, 7, 28, tzinfo=timezone.utc))
    week.verification_deadline_at = NOW - timedelta(hours=1)
    week.finalize_after = NOW - timedelta(minutes=30)
    session.commit()

    first_code, first = run_finalize_due_weeks(now=NOW, session_factory=factory, stream=io.StringIO())
    second_output = io.StringIO()
    second_code, second = run_finalize_due_weeks(
        now=NOW + timedelta(minutes=1), session_factory=factory, stream=second_output
    )

    assert first_code == second_code == 0
    assert first.finalized == 1 and first.failed == 0
    assert second.finalized == 0 and second.skipped == 1
    assert any(row.get("reason") == "already_finalized" for row in logs(second_output))


def test_multiple_prior_due_weeks_finalize(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, factory = database
    season, _, current = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    current.verification_deadline_at = NOW - timedelta(hours=1)
    current.finalize_after = NOW - timedelta(minutes=30)
    add_week(session, season, week_start=date(2026, 7, 20))

    code, summary = run_finalize_due_weeks(
        now=NOW, session_factory=factory, stream=io.StringIO()
    )

    assert code == 0 and summary.finalized == 2
    assert set(session.scalars(select(ContestWeek.status)).all()) == {"finalized"}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"week_end": date(2026, 8, 4)}, "week_not_ended"),
        ({"verification_deadline_at": NOW}, "verification_deadline_not_passed"),
        ({"finalize_after": NOW}, "finalize_after_not_passed"),
    ],
)
def test_not_due_timing_gates_are_skipped(
    database: tuple[Session, sessionmaker[Session]],
    changes: dict[str, object],
    reason: str,
) -> None:
    session, factory = database
    _, _, week = ensure_band_camp_data(session, now=datetime(2026, 7, 28, tzinfo=timezone.utc))
    week.verification_deadline_at = NOW - timedelta(hours=1)
    week.finalize_after = NOW - timedelta(minutes=30)
    for field, value in changes.items():
        setattr(week, field, value)
    session.commit()
    output = io.StringIO()

    code, summary = run_finalize_due_weeks(now=NOW, session_factory=factory, stream=output)

    assert code == 0 and summary.finalized == 0 and summary.skipped == 1
    assert any(row.get("reason") == reason for row in logs(output))
    assert session.get(ContestWeek, week.id).status == "open"


def test_all_three_contests_finalize_and_camp_points_are_open_only(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, factory = database
    _, _, week = ensure_band_camp_data(session, now=datetime(2026, 7, 28, tzinfo=timezone.utc))
    week.verification_deadline_at = NOW - timedelta(hours=1)
    week.finalize_after = NOW - timedelta(minutes=30)
    student = add_student(session, "ALL")
    add_week_activity(session, student, week)

    code, summary = run_finalize_due_weeks(
        now=NOW, session_factory=factory, stream=io.StringIO()
    )

    assert code == 0 and summary.finalized == 1
    rows = session.execute(
        select(Contest.key, ContestResult.division, ContestResult.score)
        .join(ContestResult, ContestResult.contest_id == Contest.id)
        .where(ContestResult.contest_week_id == week.id)
    ).all()
    assert set(rows) == {
        ("weekly-points-leaders", "open", 25),
        ("weekly-points-leaders", "verified", 25),
        ("weekly-practice-by-instrument", "open", 25),
        ("weekly-practice-by-instrument", "verified", 25),
        ("weekly-camp-points", "open", 1),
    }


def test_failed_week_rolls_back_continues_and_returns_nonzero_without_secrets(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, factory = database
    season, _, current = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    current.verification_deadline_at = NOW - timedelta(hours=1)
    current.finalize_after = NOW - timedelta(minutes=30)
    failed_week = add_week(session, season, week_start=date(2026, 7, 20))
    add_week_activity(session, add_student(session, "FAIL"), failed_week)
    add_week_activity(session, add_student(session, "NEXT"), current)

    def fail_after_partial(
        transaction_session: Session, *, week_start: date, now: datetime
    ) -> ContestWeek:
        result = finalize_contest_week(
            transaction_session, week_start=week_start, now=now
        )
        if week_start == failed_week.week_start:
            raise RuntimeError(
                "student@example.com PIN=1234 private note DATABASE_URL=secret"
            )
        return result

    output = io.StringIO()
    code, summary = run_finalize_due_weeks(
        now=NOW,
        session_factory=factory,
        stream=output,
        finalizer=fail_after_partial,
    )

    assert code != 0
    assert summary.failed == 1 and summary.finalized == 1
    session.expire_all()
    assert session.get(ContestWeek, failed_week.id).status == "open"
    assert session.get(ContestWeek, current.id).status == "finalized"
    assert session.scalar(select(func.count()).select_from(ContestResult).where(
        ContestResult.contest_week_id == failed_week.id
    )) == 0
    assert session.scalar(select(func.count()).select_from(RewardGrant).where(
        RewardGrant.source_key.like(f"contest:{failed_week.week_start}:%")
    )) == 0
    text = output.getvalue().casefold()
    for secret in ("student@example.com", "1234", "private note", "database_url=secret"):
        assert secret not in text
    assert '"exception_type": "runtimeerror"' in text

    retry_code, retry = run_finalize_due_weeks(
        now=NOW + timedelta(minutes=1),
        session_factory=factory,
        stream=io.StringIO(),
    )
    assert retry_code == 0
    assert retry.finalized == 1 and retry.skipped == 1 and retry.failed == 0
    session.expire_all()
    assert session.get(ContestWeek, failed_week.id).status == "finalized"


def test_finalized_historical_results_are_skipped_and_unchanged(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, factory = database
    season, contests, current = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    current.verification_deadline_at = NOW - timedelta(hours=1)
    current.finalize_after = NOW - timedelta(minutes=30)
    historical = add_week(
        session, season, week_start=date(2026, 7, 20), status="finalized"
    )
    student = add_student(session, "HISTORY")
    contest = next(item for item in contests if item.key == "weekly-points-leaders")
    result = ContestResult(
        contest_week_id=historical.id,
        contest_id=contest.id,
        division="open",
        subject_type="student",
        subject_key=str(student.id),
        profile_id=student.id,
        display_name_snapshot="Historical Winner",
        score=77,
        rank=1,
        medal="gold",
    )
    session.add(result)
    session.commit()

    code, summary = run_finalize_due_weeks(
        now=NOW, session_factory=factory, stream=io.StringIO()
    )
    session.refresh(result)

    assert code == 0 and summary.finalized == 1 and summary.skipped == 1
    assert (result.score, result.rank, result.medal, result.display_name_snapshot) == (
        77, 1, "gold", "Historical Winner"
    )
