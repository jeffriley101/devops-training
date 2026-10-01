"""Persistent authority and actual choices in disposable SQLite/PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.account_deletion import anonymize_woodchuck_account
from app.db import Base
from app.models import (PersistentTeamControl, ProfileCapability, Season, Team,
                        TeamJoinRequest, TeamMembership, TeamMembershipTransition,
                        TeamReport, WoodchuckProfile)
from app.team_authority import effective_membership, persistent_enabled
from app.teams import (active_membership, create_and_join_team, create_director_team,
                       director_team_payload, leave_team, persistent_correction_state,
                       select_team, selection_payload)
from tests.team_factory import make_team
from tests.test_team_families import disposable_url


NOW = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
BOUNDARY = datetime(2026, 10, 5, 5, tzinfo=timezone.utc)


@pytest.fixture(params=["sqlite", "postgresql"])
def authority_db(request, tmp_path):
    engine = (create_engine("sqlite://", poolclass=StaticPool)
              if request.param == "sqlite" else create_engine(disposable_url(tmp_path, request.param)))
    if request.param == "sqlite":
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        old = Season(key="old", name="Old", starts_on=date(2026, 9, 1),
                     ends_on=date(2026, 9, 27), status="closed")
        current = Season(key="current", name="Current", starts_on=date(2026, 9, 28), status="active")
        session.add_all([old, current])
        profiles = [WoodchuckProfile(woodchuck_id=f"WC-PERSIST-{n}", display_name=f"Student {n}",
                                    pin_hash="hash", instrument="Flute", level="Beginner", goal="Practice")
                    for n in range(3)]
        session.add_all(profiles)
        session.flush()
        teams = [make_team(session, season_id=old.id, display_name=f"Team {n}",
                           normalized_name=f"team {n}", emblem_key=f"letter:{chr(65+n)}",
                           is_operating=True) for n in range(2)]
        legacy = make_team(session, season_id=current.id, display_name="Legacy",
                           normalized_name="legacy", emblem_key="letter:Z")
        session.add_all([*teams, legacy])
        session.add(PersistentTeamControl(id=1, activated_at=BOUNDARY,
                                         rules_from_week_start=date(2026, 10, 5)))
        session.flush()
        factory.ids = dict(profile=profiles[0].id, owner=profiles[1].id,
                           other=profiles[2].id, old=old.id, current=current.id,
                           teams=[t.id for t in teams], legacy=legacy.id)
        session.commit()
    yield factory
    engine.dispose()


def context(factory, session):
    ids = factory.ids
    return (session.get(WoodchuckProfile, ids["profile"]),
            session.get(Season, ids["current"]),
            [session.get(Team, tid) for tid in ids["teams"]])


def carried(factory, session, *, start=BOUNDARY - timedelta(days=14)):
    row = TeamMembership(profile_id=factory.ids["profile"], team_id=factory.ids["teams"][0],
                         season_id=factory.ids["old"], is_persistent=True,
                         selected_week_start=start.date() - timedelta(days=start.weekday()), started_at=start)
    session.add(row)
    session.commit()
    return row


def test_legacy_unended_rows_do_not_compete_and_boundaries_do_not_copy(authority_db):
    with authority_db() as session:
        row = carried(authority_db, session)
        legacy = TeamMembership(profile_id=row.profile_id, team_id=authority_db.ids["legacy"],
                                season_id=authority_db.ids["current"], started_at=BOUNDARY - timedelta(days=30),
                                selected_week_start=date(2026, 9, 7))
        session.add(legacy)
        session.commit()
        for moment in (NOW, NOW + timedelta(days=7), NOW + timedelta(days=70)):
            assert active_membership(session, profile_id=row.profile_id,
                                     season_id=authority_db.ids["current"], at=moment).id == row.id
        assert legacy.ended_at is None and not legacy.is_persistent
        assert session.scalar(select(func.count(TeamMembership.id))) == 2
        assert session.scalar(select(func.count(Team.id))) == 3


def test_effective_membership_is_half_open_and_ignores_future_start(authority_db):
    with authority_db() as session:
        row = carried(authority_db, session, start=NOW + timedelta(days=1))
        assert effective_membership(session, row.profile_id, NOW) is None
        assert effective_membership(session, row.profile_id, row.started_at).id == row.id
        row.ended_at = NOW + timedelta(days=2)
        session.commit()
        assert effective_membership(session, row.profile_id, row.ended_at) is None


def test_join_switch_keeps_ids_history_and_atomic_rollback(authority_db):
    with authority_db() as session:
        profile, season, teams = context(authority_db, session)
        first, changed = select_team(session, profile=profile, season=season, team=teams[0], now=NOW)
        session.commit()
        assert changed and first.is_persistent
        first_id = first.id
        same, changed = select_team(session, profile=profile, season=season, team=teams[0], now=NOW)
        assert same.id == first_id and not changed
        second, changed = select_team(session, profile=profile, season=season, team=teams[1], now=NOW + timedelta(minutes=1))
        assert changed and first.ended_at is not None and second.team_id == teams[1].id
        session.rollback()
        assert effective_membership(session, profile.id, NOW + timedelta(minutes=2)).id == first_id
        second, _ = select_team(session, profile=profile, season=season, team=teams[1], now=NOW + timedelta(minutes=2))
        session.commit()
        assert effective_membership(session, profile.id, NOW + timedelta(minutes=1)).id == first_id
        assert effective_membership(session, profile.id, NOW + timedelta(minutes=3)).id == second.id
        assert session.scalar(select(func.count(TeamMembershipTransition.id))) == 2


def test_carried_membership_one_correction_and_leave_cannot_replenish(authority_db):
    with authority_db() as session:
        row = carried(authority_db, session)
        profile, season, teams = context(authority_db, session)
        assert persistent_correction_state(session, profile.id, NOW)[1:] == (0, 1)
        assert leave_team(session, profile=profile, season=season, now=NOW)
        session.commit()
        assert effective_membership(session, profile.id, NOW) is None
        assert not leave_team(session, profile=profile, season=season, now=NOW)
        with pytest.raises(ValueError, match="locked"):
            select_team(session, profile=profile, season=season, team=teams[1], now=NOW + timedelta(minutes=1))
        session.rollback()
        assert row.team_id == teams[0].id
        joined, changed = select_team(session, profile=profile, season=season, team=teams[1], now=NOW + timedelta(days=7))
        assert changed and joined.team_id == teams[1].id


def test_initial_choice_then_leave_spends_correction(authority_db):
    with authority_db() as session:
        profile, season, teams = context(authority_db, session)
        select_team(session, profile=profile, season=season, team=teams[0], now=BOUNDARY)
        session.commit()
        assert persistent_correction_state(session, profile.id, NOW)[1:] == (1, 2)
        assert leave_team(session, profile=profile, season=season, now=NOW)
        session.commit()
        with pytest.raises(ValueError, match="locked"):
            select_team(session, profile=profile, season=season, team=teams[0], now=NOW + timedelta(minutes=1))


def test_database_refuses_two_persistent_current_memberships(authority_db):
    with authority_db() as session:
        row = carried(authority_db, session)
        session.add(TeamMembership(profile_id=row.profile_id, team_id=authority_db.ids["teams"][1],
                                  season_id=authority_db.ids["current"], is_persistent=True,
                                  started_at=NOW, selected_week_start=date(2026, 10, 5)))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        assert effective_membership(session, row.profile_id, NOW).id == row.id


def test_new_public_and_director_teams_are_operating_across_seasons(authority_db):
    with authority_db() as session:
        profile, season, _ = context(authority_db, session)
        team, row = create_and_join_team(session, profile=profile, season=season, name="Persistent Cats",
                                        emblem_key="emoji:cat", now=NOW)
        assert team.is_operating and row.is_persistent
        later = Season(key="later", name="Later", starts_on=date(2026, 11, 2), status="active")
        session.add(later)
        session.commit()
        with pytest.raises(ValueError, match="only one Team"):
            create_and_join_team(session, profile=profile, season=later, name="Duplicate Owner",
                                 emblem_key="emoji:dog", now=NOW + timedelta(days=35))
        owner = session.get(WoodchuckProfile, authority_db.ids["owner"])
        session.add(ProfileCapability(profile_id=owner.id, capability="band_director"))
        session.commit()
        private = create_director_team(session, profile=owner, season=season, name="Director Cats",
                                       emblem_key="emoji:dog", now=NOW)
        code = private.join_code
        with pytest.raises(ValueError, match="approval"):
            select_team(session, profile=profile, season=later, team=private, now=NOW + timedelta(days=7))
        session.rollback()
        member, _ = select_team(session, profile=profile, season=later, team=private,
                                now=NOW + timedelta(days=7), private_authorized=True)
        session.commit()
        payload = director_team_payload(session, profile=owner, season=later,
                                        team_id=private.id, now=NOW + timedelta(days=7))
        assert payload["team"]["id"] == private.id and payload["team"]["join_code"] == code
        assert member.is_persistent and private.is_operating


def test_deleted_account_ends_only_persistent_authority_and_preserves_report(authority_db):
    with authority_db() as session:
        row = carried(authority_db, session)
        legacy = TeamMembership(profile_id=row.profile_id, team_id=authority_db.ids["legacy"],
                                season_id=authority_db.ids["current"], started_at=BOUNDARY - timedelta(days=30),
                                selected_week_start=date(2026, 9, 7))
        report = TeamReport(team_id=row.team_id, reporter_profile_id=row.profile_id,
                            category="other", details="WHY CAN'T I LEAVE")
        session.add_all([legacy, report])
        session.commit()
        profile, _, teams = context(authority_db, session)
        anonymize_woodchuck_account(session, profile=profile, now=NOW)
        session.commit()
        session.refresh(row)
        session.refresh(legacy)
        assert effective_membership(session, profile.id, NOW) is None
        assert legacy.ended_at is None and row.ended_at is not None
        assert report.category == "other" and report.details == "WHY CAN'T I LEAVE"
        with pytest.raises(ValueError, match="cannot select"):
            select_team(session, profile=profile, season=session.get(Season, authority_db.ids["current"]),
                        team=teams[1], now=NOW + timedelta(minutes=1))


def test_pre_activation_stays_legacy_without_enabling_control(authority_db):
    with authority_db() as session:
        control = session.get(PersistentTeamControl, 1)
        control.activated_at = None
        control.rules_from_week_start = None
        session.commit()
        assert not persistent_enabled(session, NOW)
        profile, season, _ = context(authority_db, session)
        team = session.get(Team, authority_db.ids["legacy"])
        row, _ = select_team(session, profile=profile, season=season, team=team, now=NOW)
        session.commit()
        assert not row.is_persistent
        assert session.get(PersistentTeamControl, 1).activated_at is None


def test_persistent_operations_do_not_require_presentation_season(authority_db):
    with authority_db() as session:
        profile, _, teams = context(authority_db, session)
        current = session.get(Season, authority_db.ids["current"])
        current.status = "closed"
        session.commit()
        row, _ = select_team(session, profile=profile, season=None, team=teams[0], now=NOW)
        session.commit()
        assert row.is_persistent and row.season_id is None
        payload = selection_payload(session, profile=profile, now=NOW)
        assert payload["membership"]["team"]["id"] == teams[0].id
        assert payload["season"]["key"] is None
        assert leave_team(session, profile=profile, season=None, now=NOW + timedelta(minutes=1))


@pytest.mark.parametrize("different_targets", [False, True])
def test_concurrent_carried_switches_allow_one_and_keep_one_authority(authority_db, different_targets):
    with authority_db() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("Real row-lock concurrency requires PostgreSQL")
        carried(authority_db, session)
        extra = session.get(Team, authority_db.ids["legacy"])
        extra.is_operating = True
        session.commit()
    ready = Barrier(2)
    def switch(index):
        with authority_db() as session:
            profile, season, teams = context(authority_db, session)
            ready.wait(timeout=5)
            try:
                target = (session.get(Team, authority_db.ids["legacy"])
                          if different_targets and index else teams[1])
                row, changed = select_team(session, profile=profile, season=season,
                                           team=target, now=NOW)
                session.commit()
                return row.id, changed
            except ValueError:
                session.rollback()
                return None, False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(switch, (0, 1)))
    assert sum(changed for _, changed in results) == 1
    if different_targets:
        assert sum(row_id is None for row_id, _ in results) == 1
    else:
        assert results[0][0] == results[1][0]
    with authority_db() as session:
        assert session.scalar(select(func.count(TeamMembership.id)).where(
            TeamMembership.is_persistent.is_(True), TeamMembership.ended_at.is_(None))) == 1
        assert session.scalar(select(func.count(TeamMembershipTransition.id))) == 1


def test_concurrent_switch_and_deletion_cannot_restore_authority(authority_db):
    with authority_db() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("Real row-lock concurrency requires PostgreSQL")
        carried(authority_db, session)
    ready = Barrier(2)
    def worker(delete):
        with authority_db() as session:
            profile, season, teams = context(authority_db, session)
            ready.wait(timeout=5)
            try:
                if delete:
                    anonymize_woodchuck_account(session, profile=profile, now=NOW)
                else:
                    select_team(session, profile=profile, season=season, team=teams[1], now=NOW)
                session.commit()
            except ValueError:
                assert not delete
                session.rollback()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker, (True, False)))
    with authority_db() as session:
        profile = session.get(WoodchuckProfile, authority_db.ids["profile"])
        assert profile.status == "deleted"
        assert effective_membership(session, profile.id, NOW) is None
