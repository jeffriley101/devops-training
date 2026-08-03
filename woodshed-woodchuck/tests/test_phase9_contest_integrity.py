from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.account_deletion import DELETED_PUBLIC_NAME, anonymize_woodchuck_account
from app.contest_jobs import audit_or_repair_history
from app.contests import (
    contest_results_payload,
    ensure_band_camp_data,
    finalize_contest_week,
    team_leaderboards,
    weekly_camp_points,
    weekly_student_points,
)
from app.db import Base
from app.models import (
    CampPointAward,
    ContestResult,
    CrownProgress,
    PracticeChart,
    PracticeChartVerification,
    RewardGrant,
    Team,
    WoodchuckProfile,
    WoodchuckState,
)
from app.security import hash_pin
from app.teams import create_and_join_team


NOW = datetime(2026, 7, 28, 15, tzinfo=timezone.utc)
DELETED_AT = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
FINAL_NOW = datetime(2026, 8, 3, 18, tzinfo=timezone.utc)


def database() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def add_profile(session: Session, number: int) -> WoodchuckProfile:
    row = WoodchuckProfile(
        woodchuck_id=f"WC-INTEGRITY-{number}",
        display_name=f"Integrity Student {number}",
        pin_hash=hash_pin("1234"), instrument="Flute", level="Beginner",
        goal="Practice",
    )
    session.add(row); session.flush()
    session.add(WoodchuckState(profile_id=row.id, state_json={}, revision=0))
    return row


def add_chart(session: Session, profile: WoodchuckProfile, team: Team) -> PracticeChart:
    row = PracticeChart(
        profile_id=profile.id, practice_date=date(2026, 7, 29), minutes=42,
        instrument="Flute", practice_details=[], source="p-book", credits_awarded=0,
        include_contests=True, include_team_contests=True, team_id=team.id,
    )
    session.add(row); session.flush()
    return row


def test_deleted_student_leaves_live_individual_boards_but_not_team_totals() -> None:
    session = database()
    season, _contests, week = ensure_band_camp_data(session, now=NOW)
    student = add_profile(session, 1); session.commit()
    team, _ = create_and_join_team(
        session, profile=student, season=season, name="Durable Totals",
        emblem_key="shield:gold", now=NOW,
    )
    add_chart(session, student, team)
    session.add(CampPointAward(
        profile_id=student.id, activity_type="care", points_awarded=1,
        occurred_at=NOW, duplicate_key="integrity-live", team_id=team.id,
    ))
    session.commit()

    anonymize_woodchuck_account(session, profile=student, now=DELETED_AT)
    session.commit()

    assert weekly_student_points(
        session, contest_week=week, current_profile_id=student.id
    )["open"] == []
    assert weekly_camp_points(
        session, contest_week=week, current_profile_id=student.id
    )["open"] == []
    team_row = team_leaderboards(
        session, season=season, contest_week=week
    )["team-weekly-practice"]["open"][0]
    assert team_row["team_name"] == "Durable Totals"
    assert team_row["score"] == 42
    assert session.scalar(select(PracticeChart)).team_id == team.id


def test_hidden_team_identity_is_masked_without_changing_result_or_score() -> None:
    session = database()
    season, _contests, week = ensure_band_camp_data(session, now=NOW)
    student = add_profile(session, 2); session.commit()
    team, _ = create_and_join_team(
        session, profile=student, season=season, name="Private Original",
        emblem_key="emoji:goat", now=NOW,
    )
    add_chart(session, student, team); session.commit()
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    original = session.scalar(select(ContestResult).where(
        ContestResult.subject_type == "team"
    ))
    stored_name, stored_score = original.display_name_snapshot, original.score
    reward_count = session.scalar(select(func.count()).select_from(RewardGrant))

    team.moderation_status = "hidden"; session.commit()
    live = team_leaderboards(session, season=season, contest_week=week)
    assert live["team-weekly-practice"]["open"][0]["team_name"] == "Hidden Team"
    assert live["team-weekly-practice"]["open"][0]["emblem_key"] == "shield:silver"
    history = contest_results_payload(session, week)["results"]
    team_rows = [row for row in history if row["subject_type"] == "team"]
    assert team_rows and all(row["team_name"] == "Hidden Team" for row in team_rows)
    assert all(row["emblem_key"] == "shield:silver" for row in team_rows)
    session.refresh(original)
    assert (original.display_name_snapshot, original.score) == (stored_name, stored_score)
    assert session.scalar(select(func.count()).select_from(RewardGrant)) == reward_count


def test_open_week_finalizes_after_deletion_and_reruns_without_duplicates() -> None:
    session = database()
    season, _contests, week = ensure_band_camp_data(session, now=NOW)
    student = add_profile(session, 3); session.commit()
    team, _ = create_and_join_team(
        session, profile=student, season=season, name="Finalizer Survives",
        emblem_key="letter:F", now=NOW,
    )
    chart = add_chart(session, student, team); session.commit()
    anonymize_woodchuck_account(session, profile=student, now=DELETED_AT)
    session.commit()

    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    student_results = session.scalars(select(ContestResult).where(
        ContestResult.profile_id == student.id
    )).all()
    assert student_results
    assert all(row.display_name_snapshot == DELETED_PUBLIC_NAME for row in student_results)
    assert session.get(PracticeChart, chart.id).minutes == 42
    counts = (
        session.scalar(select(func.count()).select_from(ContestResult)),
        session.scalar(select(func.count()).select_from(RewardGrant)),
        session.scalar(select(func.count()).select_from(CampPointAward)),
    )
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    audit_or_repair_history(
        session, week_start=week.week_start, apply=True, now=FINAL_NOW
    )
    session.commit()
    assert counts == (
        session.scalar(select(func.count()).select_from(ContestResult)),
        session.scalar(select(func.count()).select_from(RewardGrant)),
        session.scalar(select(func.count()).select_from(CampPointAward)),
    )


def test_open_and_verified_wins_share_one_crown_progress_row() -> None:
    session = database()
    season, _contests, week = ensure_band_camp_data(session, now=NOW)
    student = add_profile(session, 4); session.commit()
    team, _ = create_and_join_team(
        session, profile=student, season=season, name="Dual Division",
        emblem_key="letter:D", now=NOW,
    )
    chart = add_chart(session, student, team)
    session.add(PracticeChartVerification(
        practice_chart_id=chart.id, status="approved",
        responded_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    ))
    session.commit()

    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    progress = session.scalars(select(CrownProgress).where(
        CrownProgress.profile_id == student.id,
        CrownProgress.category_key == "weekly-points-leaders",
    )).all()
    assert len(progress) == 1
    assert progress[0].qualifying_wins == 2
