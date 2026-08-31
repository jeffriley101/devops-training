from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import contest_jobs
from app.contest_jobs import (
    JobSummary,
    audit_or_repair_history,
    run_finalize_due_weeks,
)
from app.contests import (
    ensure_band_camp_data,
    finalize_contest_week,
    finalized_weeks_payload,
    hall_of_champions_payload,
)
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
    WoodchuckState,
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
    assert contest_jobs.main(["rollover_season"]) == 2
    assert contest_jobs.main([]) == 2
    usage = capsys.readouterr().err
    assert "python -m app.contest_jobs" in usage
    assert "finalize_due_weeks" in usage
    assert "audit_history" in usage
    assert "rollover_season" in usage


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
    *,
    created_at: datetime | None = None,
) -> None:
    chart = PracticeChart(
        profile_id=profile.id,
        practice_date=week.week_start,
        minutes=25,
        instrument=profile.instrument,
        practice_details=[],
        source="p-book",
        credits_awarded=0,
        created_at=created_at,
    )
    session.add(chart)
    session.flush()
    session.add(PracticeChartVerification(
        practice_chart_id=chart.id,
        verifier_id=None,
        status="approved",
        responded_at=week.verification_deadline_at - timedelta(minutes=1),
    ))
    session.add(CampPointAward(
        profile_id=profile.id,
        activity_type="care",
        points_awarded=1,
        occurred_at=datetime.combine(
            week.week_start, datetime.min.time(), timezone.utc
        ) + timedelta(hours=18),
        duplicate_key=f"job:{week.week_start}:care",
        created_at=created_at,
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
    add_week_activity(
        session, student, week,
        created_at=datetime(2026, 7, 28, 15, tzinfo=timezone.utc),
    )

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


def test_integrity_error_logs_only_driver_diagnostics_and_rolls_back(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, factory = database
    _, _, week = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    week.verification_deadline_at = NOW - timedelta(hours=1)
    week.finalize_after = NOW - timedelta(minutes=30)
    session.commit()

    class Diagnostic:
        sqlstate = "23505"
        constraint_name = "uq_crown_progress_profile_category"
        table_name = "crown_progress"
        column_name = "category_key"

    class DatabaseError(Exception):
        sqlstate = "23505"
        diag = Diagnostic()

    integrity_error = IntegrityError(
        "INSERT INTO secret_table VALUES (%(secret)s)",
        {"secret": "bound-parameter-secret"},
        DatabaseError("raw database message with student@example.com and row values"),
    )

    def fail_during_crown_progress(
        transaction_session: Session, *, week_start: date, now: datetime
    ) -> ContestWeek:
        transaction_week = transaction_session.scalar(select(ContestWeek).where(
            ContestWeek.week_start == week_start
        ))
        assert transaction_week is not None
        transaction_week.status = "finalized"
        transaction_session.flush()
        transaction_session.info["contest_finalization_stage"] = "crown_progress"
        raise integrity_error

    output = io.StringIO()
    code, summary = run_finalize_due_weeks(
        now=NOW, session_factory=factory, stream=output,
        finalizer=fail_during_crown_progress,
    )

    assert code == 1 and summary.failed == 1 and summary.finalized == 0
    session.expire_all()
    assert session.get(ContestWeek, week.id).status == "open"
    records = logs(output)
    diagnostic_record = next(
        record for record in records
        if record["event"] == "week_integrity_error"
    )
    assert diagnostic_record == {
        "event": "week_integrity_error",
        "week_start": "2026-07-27",
        "stage": "crown_progress",
        "sqlstate": "23505",
        "constraint": "uq_crown_progress_profile_category",
        "table": "crown_progress",
        "column": "category_key",
    }
    serialized = output.getvalue().casefold()
    for sensitive in (
        "insert into", "secret_table", "bound-parameter-secret",
        "raw database message", "student@example.com", "row values",
    ):
        assert sensitive not in serialized


def test_successful_finalization_log_shape_is_unchanged(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, factory = database
    _, _, week = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    week.verification_deadline_at = NOW - timedelta(hours=1)
    week.finalize_after = NOW - timedelta(minutes=30)
    session.commit()
    output = io.StringIO()

    code, summary = run_finalize_due_weeks(
        now=NOW, session_factory=factory, stream=output
    )

    assert code == 0 and summary.finalized == 1 and summary.failed == 0
    records = logs(output)
    assert [record["event"] for record in records] == [
        "run_started",
        "due_weeks_found",
        "week_finalizing",
        "week_finalized",
        "run_finished",
    ]
    assert records[3] == {
        "event": "week_finalized", "week_start": "2026-07-27"
    }
    session.expire_all()
    assert session.get(ContestWeek, week.id).status == "finalized"


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


def test_recently_closed_week_audit_respects_deadline_then_finalizes(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _factory = database
    _, _, week = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    student = add_student(session, "RECENT")
    add_week_activity(
        session, student, week,
        created_at=datetime(2026, 7, 28, 15, tzinfo=timezone.utc),
    )
    source_ids = set(session.scalars(select(PracticeChart.id)).all())

    early = audit_or_repair_history(
        session,
        week_start=week.week_start,
        now=week.finalize_after - timedelta(minutes=1),
    )
    assert early["action"] == "not_due"
    assert early["reason"] == "finalize_after_not_passed"
    assert session.get(ContestWeek, week.id).status == "open"

    dry_run = audit_or_repair_history(
        session, week_start=week.week_start, now=NOW, apply=False
    )
    assert dry_run["created"]["results"] == 4
    assert session.scalar(select(func.count()).select_from(ContestResult)) == 0

    applied = audit_or_repair_history(
        session, week_start=week.week_start, now=NOW, apply=True
    )
    session.commit()
    assert applied["action"] == "repaired"
    assert session.get(ContestWeek, week.id).status == "finalized"
    assert finalized_weeks_payload(session)["weeks"][0]["week_start"] == str(
        week.week_start
    )
    hall = hall_of_champions_payload(session)
    assert any(row["display_name"] == student.display_name for row in hall["students"])
    assert {"open", "verified"}.issubset(
        set(next(row for row in hall["students"] if row["display_name"] == student.display_name)["divisions"])
    )
    assert set(session.scalars(select(PracticeChart.id)).all()) == source_ids


def test_incomplete_finalized_week_repairs_once_without_duplicate_rewards(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _factory = database
    season, _, current = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    incomplete = add_week(
        session, season, week_start=date(2026, 7, 20), status="finalized"
    )
    student = add_student(session, "REPAIR")
    add_week_activity(session, student, incomplete)
    source_chart = session.scalar(select(PracticeChart).where(
        PracticeChart.profile_id == student.id
    ))
    source_chart.created_at = incomplete.finalized_at - timedelta(days=1)
    source_award = session.scalar(select(CampPointAward).where(
        CampPointAward.profile_id == student.id
    ))
    source_award.created_at = incomplete.finalized_at - timedelta(days=1)
    late_student = add_student(session, "LATE")
    session.add(PracticeChart(
        profile_id=late_student.id, practice_date=incomplete.week_start,
        minutes=999, instrument=late_student.instrument, practice_details=[],
        source="p-book", credits_awarded=0, include_contests=True,
        created_at=NOW + timedelta(days=1),
    ))
    session.commit()
    source_rows = [tuple(row) for row in session.execute(select(
        PracticeChart.id, PracticeChart.profile_id, PracticeChart.minutes,
        PracticeChart.practice_date,
    )).all()]

    dry_run = audit_or_repair_history(
        session, week_start=incomplete.week_start, now=NOW, apply=False
    )
    assert dry_run["action"] == "repaired"
    assert dry_run["created"]["results"] == 4
    assert session.scalar(select(func.count()).select_from(ContestResult)) == 0

    first = audit_or_repair_history(
        session, week_start=incomplete.week_start, now=NOW, apply=True
    )
    session.commit()
    counts = {
        "results": session.scalar(select(func.count()).select_from(ContestResult)),
        "grants": session.scalar(select(func.count()).select_from(RewardGrant)),
        "camp": session.scalar(select(func.count()).select_from(CampPointAward)),
    }
    state = session.get(WoodchuckState, student.id)
    balance = state.state_json["progress"]["credits"]

    second = audit_or_repair_history(
        session, week_start=incomplete.week_start, now=NOW + timedelta(days=1),
        apply=True,
    )
    session.commit()
    assert first["action"] == "repaired"
    assert not session.scalars(select(ContestResult).where(
        ContestResult.profile_id == late_student.id
    )).all()
    assert second["action"] == "unchanged"
    assert all(value == 0 for value in second["created"].values())
    assert counts == {
        "results": session.scalar(select(func.count()).select_from(ContestResult)),
        "grants": session.scalar(select(func.count()).select_from(RewardGrant)),
        "camp": session.scalar(select(func.count()).select_from(CampPointAward)),
    }
    assert session.get(WoodchuckState, student.id).state_json["progress"]["credits"] == balance
    assert [tuple(row) for row in session.execute(select(
        PracticeChart.id, PracticeChart.profile_id, PracticeChart.minutes,
        PracticeChart.practice_date,
    )).all()] == source_rows
    assert session.get(ContestWeek, current.id).status == "open"


def test_complete_finalized_week_repair_is_unchanged(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _factory = database
    _, _, week = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    add_week_activity(session, add_student(session, "COMPLETE"), week)
    finalize_contest_week(session, week_start=week.week_start, now=NOW)
    session.commit()

    report = audit_or_repair_history(
        session, week_start=week.week_start, now=NOW + timedelta(days=1), apply=True
    )
    session.commit()
    assert report["action"] == "unchanged"
    assert all(value == 0 for value in report["created"].values())
