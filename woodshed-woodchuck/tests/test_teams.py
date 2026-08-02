from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.contests import create_camp_point_award
from app.db import Base
from app.models import PracticeChart, Season, Team, TeamMembership, WoodchuckProfile
from app.practice_charts import create_practice_chart_verification_request
from app.team_names import InvalidTeamName, normalized_team_name
from app.teams import (
    APPROVED_EMBLEMS,
    active_membership,
    create_and_join_team,
    membership_at,
    select_team,
    team_payload,
)


NOW = datetime(2026, 7, 28, 15, tzinfo=timezone.utc)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def profile(session: Session, number: int) -> WoodchuckProfile:
    row = WoodchuckProfile(
        woodchuck_id=f"WC-TEAM-{number}", display_name=f"Player {number}",
        pin_hash="hash", instrument="Trumpet", level="Beginner", goal="Practice",
    )
    session.add(row); session.flush()
    return row


def season(session: Session) -> Season:
    row = Season(
        key="team-test", name="Team Test", timezone="America/Chicago",
        starts_on=date(2026, 7, 27), ends_on=None, status="active",
    )
    session.add(row); session.commit()
    return row


@pytest.mark.parametrize("name", ["", "   ", "⭐⭐", "Admin", "W00dsh3d", "sh!t squad"])
def test_team_name_rejects_empty_symbol_reserved_and_disguised_names(name: str) -> None:
    with pytest.raises(InvalidTeamName):
        normalized_team_name(name)


def test_team_name_normalizes_unicode_case_and_spacing() -> None:
    display, normalized = normalized_team_name("  The   Brass  Cats ")
    assert display == "The Brass Cats"
    assert normalized == "the brass cats"


def test_emblems_are_server_controlled_and_include_required_families() -> None:
    assert APPROVED_EMBLEMS["emoji:cat"] == "🐱"
    assert APPROVED_EMBLEMS["emoji:dog"] == "🐶"
    assert APPROVED_EMBLEMS["letter:A"] == "A"
    assert APPROVED_EMBLEMS["letter:Z"] == "Z"
    assert APPROVED_EMBLEMS["shield:gold"] == "Gold"


def test_creator_can_create_once_and_public_payload_is_safe(session: Session) -> None:
    active = season(session); captain = profile(session, 1); session.commit()
    team, membership = create_and_join_team(
        session, profile=captain, season=active, name="Brass Cats",
        emblem_key="emoji:cat", now=NOW,
    )
    assert membership.team_id == team.id
    assert active_membership(session, profile_id=captain.id, season_id=active.id).team_id == team.id
    public = team_payload(team, captain)
    assert public["captain"]["accessible_label"] == "Team Captain"
    assert "email" not in repr(public).casefold()
    assert "pin" not in repr(public).casefold()
    with pytest.raises(ValueError, match="only one"):
        create_and_join_team(
            session, profile=captain, season=active, name="Second Team",
            emblem_key="emoji:dog", now=NOW,
        )


def test_duplicate_normalized_name_and_emblem_are_rejected(session: Session) -> None:
    active = season(session); first = profile(session, 1); second = profile(session, 2); third = profile(session, 3)
    session.commit()
    create_and_join_team(session, profile=first, season=active, name="Brass Cats", emblem_key="emoji:cat", now=NOW)
    with pytest.raises(ValueError, match="already in use"):
        create_and_join_team(session, profile=second, season=active, name=" BRASS   CATS ", emblem_key="emoji:dog", now=NOW)
    with pytest.raises(ValueError, match="already in use"):
        create_and_join_team(session, profile=third, season=active, name="Dog Tones", emblem_key="emoji:cat", now=NOW)


def test_membership_switch_is_weekly_idempotent_and_historical(session: Session) -> None:
    active = season(session); owner1 = profile(session, 1); owner2 = profile(session, 2); member = profile(session, 3); session.commit()
    team1, _ = create_and_join_team(session, profile=owner1, season=active, name="Team One", emblem_key="letter:A", now=NOW)
    team2, _ = create_and_join_team(session, profile=owner2, season=active, name="Team Two", emblem_key="letter:B", now=NOW)
    first, changed = select_team(session, profile=member, season=active, team=team1, now=NOW)
    session.commit()
    assert changed
    same, changed = select_team(session, profile=member, season=active, team=team1, now=NOW)
    assert same.id == first.id and not changed
    with pytest.raises(ValueError, match="locked"):
        select_team(session, profile=member, season=active, team=team2, now=NOW)
    later = NOW + timedelta(days=7)
    second, changed = select_team(session, profile=member, season=active, team=team2, now=later)
    session.commit()
    assert changed and second.team_id == team2.id
    assert membership_at(session, profile_id=member.id, season_id=active.id, at=NOW + timedelta(hours=1)).team_id == team1.id
    assert membership_at(session, profile_id=member.id, season_id=active.id, at=later + timedelta(hours=1)).team_id == team2.id
    assert session.scalars(select(TeamMembership).where(TeamMembership.profile_id == member.id)).all().__len__() == 2


def test_chart_and_camp_point_snapshot_team_without_rewriting_history(session: Session) -> None:
    active = season(session); captain1 = profile(session, 1); captain2 = profile(session, 2); member = profile(session, 3); session.commit()
    team1, _ = create_and_join_team(session, profile=captain1, season=active, name="Team One", emblem_key="shield:blue", now=NOW)
    team2, _ = create_and_join_team(session, profile=captain2, season=active, name="Team Two", emblem_key="shield:red", now=NOW)
    select_team(session, profile=member, season=active, team=team1, now=NOW); session.commit()
    chart = create_practice_chart_verification_request(
        session, profile=member, verifier_id=None, practice_date=NOW.date(), minutes=30,
        include_team_contests=True, team_id=team1.id,
    ).chart
    award, created = create_camp_point_award(
        session, profile=member, activity_type="care", activity_date=NOW.astimezone().date(), now=NOW,
    )
    session.commit()
    assert created and chart.team_id == team1.id and award.team_id == team1.id
    select_team(session, profile=member, season=active, team=team2, now=NOW + timedelta(days=7)); session.commit()
    session.refresh(chart); session.refresh(award)
    assert chart.team_id == team1.id and award.team_id == team1.id
    no_team = create_practice_chart_verification_request(
        session, profile=member, verifier_id=None, practice_date=NOW.date(), minutes=10,
        include_team_contests=False, team_id=team2.id,
    ).chart
    assert no_team.team_id is None
