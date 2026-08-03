from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.contests import (
    create_camp_point_award, ensure_band_camp_data, finalize_contest_week,
    hall_of_champions_payload, team_leaderboards,
)
from app.contest_jobs import audit_or_repair_history
from app.db import Base
from app.models import (
    CampPointAward, Contest, ContestResult, CrownProgress, PracticeChart,
    PracticeChartVerification, RewardGrant, TeamWeekMembershipSnapshot,
    WoodchuckProfile, WoodchuckState,
)
from app.teams import create_and_join_team, select_team


NOW = datetime(2026, 7, 28, 15, tzinfo=timezone.utc)
FINAL_NOW = datetime(2026, 8, 3, 18, tzinfo=timezone.utc)


def database() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def add_profile(session: Session, number: int) -> WoodchuckProfile:
    row = WoodchuckProfile(
        woodchuck_id=f"WC-TC-{number}", display_name=f"Team Player {number}",
        pin_hash="hash", instrument="Flute", level="Beginner", goal="Practice",
    )
    session.add(row); session.flush()
    return row


def add_chart(session: Session, profile: WoodchuckProfile, team_id: int, minutes: int, *, approved: bool = True, practice_date: date = date(2026, 7, 29)) -> PracticeChart:
    chart = PracticeChart(
        profile_id=profile.id, practice_date=practice_date, minutes=minutes,
        instrument=profile.instrument, practice_details=[], source="p-book",
        credits_awarded=0, include_contests=True, include_team_contests=True,
        team_id=team_id,
    )
    session.add(chart); session.flush()
    if approved:
        session.add(PracticeChartVerification(
            practice_chart_id=chart.id, status="approved",
            responded_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        ))
    session.flush()
    return chart


def test_team_practice_cap_average_verified_and_season_formulas() -> None:
    session = database()
    season, _, week = ensure_band_camp_data(session, now=NOW)
    captain = add_profile(session, 1); member = add_profile(session, 2); session.commit()
    team, _ = create_and_join_team(session, profile=captain, season=season, name="Cap Team", emblem_key="emoji:goat", now=NOW)
    select_team(session, profile=member, season=season, team=team, now=NOW); session.commit()
    add_chart(session, captain, team.id, 200)
    add_chart(session, captain, team.id, 150)
    add_chart(session, member, team.id, 100, approved=False)
    session.commit()

    boards = team_leaderboards(session, season=season, contest_week=week)
    weekly_open = boards["team-weekly-practice"]["open"][0]
    weekly_verified = boards["team-weekly-practice"]["verified"][0]
    assert weekly_open["score"] == 400 and weekly_open["active_member_count"] == 2
    assert weekly_verified["score"] == 300 and weekly_verified["active_member_count"] == 1
    assert boards["team-average-practice"]["open"][0]["score"] == 20000
    assert boards["team-average-practice"]["verified"][0]["score"] == 30000
    assert boards["team-season-practice"]["open"][0]["score"] == 400


def test_finalization_snapshots_membership_and_rewards_every_team_board_once() -> None:
    session = database()
    season, _, week = ensure_band_camp_data(session, now=NOW)
    captain = add_profile(session, 1); noncontributor = add_profile(session, 2); session.commit()
    team, _ = create_and_join_team(session, profile=captain, season=season, name="Final Team", emblem_key="shield:gold", now=NOW)
    select_team(session, profile=noncontributor, season=season, team=team, now=NOW)
    add_chart(session, noncontributor, team.id, 5, practice_date=date(2026, 7, 20))
    add_chart(session, captain, team.id, 45)
    create_camp_point_award(session, profile=captain, activity_type="care", activity_date=NOW.date(), now=NOW)
    session.commit()

    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW); session.commit()
    team_results = session.scalars(select(ContestResult).where(ContestResult.subject_type == "team")).all()
    assert len(team_results) == 8
    assert {row.division for row in team_results} == {"open", "verified"}
    assert {session.get(Contest, row.contest_id).key for row in team_results} == {
        "team-weekly-practice", "team-average-practice",
        "team-season-practice", "team-seasonal-points",
    }
    assert session.scalar(select(func.count()).select_from(TeamWeekMembershipSnapshot)) == 2
    for profile in (captain, noncontributor):
        team_dandelions = session.scalars(select(RewardGrant).where(
            RewardGrant.profile_id == profile.id,
            RewardGrant.source_key.like("%:team-%"),
            RewardGrant.reward_type == "dandelion",
        )).all()
        assert len(team_dandelions) == 8
        assert all(grant.amount == 50 for grant in team_dandelions)
        crown = session.scalar(select(CrownProgress).where(
            CrownProgress.profile_id == profile.id,
            CrownProgress.category_key == "team-crown",
        ))
        assert crown is not None and crown.qualifying_wins == 8
    first_counts = (
        session.scalar(select(func.count()).select_from(RewardGrant)),
        session.scalar(select(func.count()).select_from(CampPointAward)),
        session.scalar(select(func.count()).select_from(ContestResult)),
    )
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW); session.commit()
    assert first_counts == (
        session.scalar(select(func.count()).select_from(RewardGrant)),
        session.scalar(select(func.count()).select_from(CampPointAward)),
        session.scalar(select(func.count()).select_from(ContestResult)),
    )
    hall = hall_of_champions_payload(session)
    assert next(row for row in hall["students"] if row["display_name"] == noncontributor.display_name)["medals"]["gold"] == 8


def test_revised_olympic_rewards_and_weekly_participation() -> None:
    session = database(); season, _, week = ensure_band_camp_data(session, now=NOW)
    students = [add_profile(session, number) for number in range(1, 5)]; session.commit()
    for index, student in enumerate(students):
        team, _ = create_and_join_team(
            session, profile=student, season=season, name=f"Solo {index}",
            emblem_key=f"letter:{chr(65 + index)}", now=NOW,
        )
        add_chart(session, student, team.id, [60, 60, 40, 20][index], approved=False)
    session.commit(); finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW); session.commit()
    open_results = session.scalars(select(ContestResult).join(Contest).where(
        Contest.key == "weekly-points-leaders", ContestResult.division == "open",
    ).order_by(ContestResult.id)).all()
    assert [row.rank for row in open_results] == [1, 1, 3]
    expected = {students[0].id: 50, students[1].id: 50, students[2].id: 15}
    for profile_id, amount in expected.items():
        grant = session.scalar(select(RewardGrant).where(
            RewardGrant.profile_id == profile_id,
            RewardGrant.contest_result_id.in_([row.id for row in open_results]),
            RewardGrant.reward_type == "dandelion",
        ))
        assert grant is not None and grant.amount == amount
    participation = session.scalars(select(RewardGrant).where(
        RewardGrant.reward_type == "participation_dandelion"
    )).all()
    assert len(participation) == 4
    assert all(row.amount == 5 for row in participation)
    assert not session.scalars(select(CampPointAward).where(
        CampPointAward.activity_type == "participation"
    )).all()


def test_repair_preserves_team_and_individual_history_without_duplicates() -> None:
    session = database()
    season, _, week = ensure_band_camp_data(session, now=NOW)
    captain = add_profile(session, 41)
    member = add_profile(session, 42)
    session.commit()
    team, _ = create_and_join_team(
        session, profile=captain, season=season, name="Repair Band",
        emblem_key="letter:R", now=NOW,
    )
    select_team(session, profile=member, season=season, team=team, now=NOW)
    add_chart(session, captain, team.id, 45)
    add_chart(session, member, team.id, 20)
    week.status = "finalized"
    week.finalized_at = FINAL_NOW
    session.commit()
    chart_ids = set(session.scalars(select(PracticeChart.id)).all())

    first = audit_or_repair_history(
        session, week_start=week.week_start, now=FINAL_NOW, apply=True
    )
    session.commit()
    results = session.scalars(select(ContestResult).where(
        ContestResult.contest_week_id == week.id
    )).all()
    assert first["action"] == "repaired"
    assert {row.subject_type for row in results} >= {"student", "instrument", "team"}
    assert {row.division for row in results} == {"open", "verified"}
    first_counts = (
        len(results),
        session.scalar(select(func.count()).select_from(RewardGrant)),
        session.scalar(select(func.count()).select_from(CampPointAward)),
        session.scalar(select(func.sum(CrownProgress.qualifying_wins))),
    )

    second = audit_or_repair_history(
        session, week_start=week.week_start, now=FINAL_NOW, apply=True
    )
    session.commit()
    assert second["action"] == "unchanged"
    assert first_counts == (
        session.scalar(select(func.count()).select_from(ContestResult)),
        session.scalar(select(func.count()).select_from(RewardGrant)),
        session.scalar(select(func.count()).select_from(CampPointAward)),
        session.scalar(select(func.sum(CrownProgress.qualifying_wins))),
    )
    assert set(session.scalars(select(PracticeChart.id)).all()) == chart_ids
    hall = hall_of_champions_payload(session)
    assert any(row["display_name"] == captain.display_name for row in hall["students"])
