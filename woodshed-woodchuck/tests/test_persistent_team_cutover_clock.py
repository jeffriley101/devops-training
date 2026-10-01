"""Request timestamps must be sampled after waiting for the cutover fence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app import account_routes, team_authority, teams
from app.models import PersistentTeamControl, Season, Team, WoodchuckProfile
from tests.test_persistent_team_authority import authority_db, BOUNDARY


def test_waiting_team_request_uses_post_cutover_clock(authority_db, monkeypatch):
    with authority_db() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("Cutover wait test requires real PostgreSQL row locks")
        control = session.get(PersistentTeamControl, 1)
        control.activated_at = control.rules_from_week_start = None
        session.commit()
    held, entered = Event(), Event()
    clock = {"at": BOUNDARY - timedelta(seconds=1)}
    original = team_authority.lock_authority
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["at"].astimezone(tz) if tz else clock["at"].replace(tzinfo=None)
    def fence(session):
        entered.set()
        return original(session)
    monkeypatch.setattr(teams, "datetime", Clock)
    monkeypatch.setattr(teams, "lock_authority", fence)
    monkeypatch.setattr(teams, "current_profile", lambda request, session:
                        session.get(WoodchuckProfile, authority_db.ids["profile"]))
    monkeypatch.setattr(teams, "ensure_current_contest_data", lambda session, now:
                        (session.get(Season, authority_db.ids["current"]), None,
                         SimpleNamespace(week_start=BOUNDARY.date())))
    def cutover():
        with authority_db() as session:
            control = original(session)
            held.set()
            assert entered.wait(5), "Team request did not enter the fence"
            control.activated_at = BOUNDARY
            control.rules_from_week_start = BOUNDARY.date()
            clock["at"] = BOUNDARY + timedelta(seconds=1)
            session.commit()
    def join():
        with authority_db() as session:
            request = Request({"type": "http", "method": "POST", "path": "/teams/selection"})
            profile, season, now = teams.authenticated_context(request, session)
            team = session.get(Team, authority_db.ids["teams"][0])
            row, _ = teams.select_team(session, profile=profile, season=season, team=team, now=now)
            session.commit()
            return row.is_persistent, row.started_at
    with ThreadPoolExecutor(max_workers=2) as pool:
        applying = pool.submit(cutover)
        assert held.wait(5)
        joining = pool.submit(join)
        applying.result(timeout=15)
        persistent, started_at = joining.result(timeout=15)
    assert persistent
    assert team_authority.utc(started_at) == BOUNDARY + timedelta(seconds=1)


def test_delete_route_acquires_authority_before_runtime_clock(authority_db, monkeypatch):
    acquired = []
    original = team_authority.lock_authority
    def fence(session):
        result = original(session)
        acquired.append(True)
        return result
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert acquired, "Deletion timestamp was captured before its fence"
            instant = BOUNDARY + timedelta(seconds=1)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(team_authority, "lock_authority", fence)
    monkeypatch.setattr(account_routes, "datetime", Clock)
    monkeypatch.setattr(account_routes, "SessionLocal", authority_db)
    monkeypatch.setattr(account_routes, "current_profile", lambda request, session:
                        session.get(WoodchuckProfile, authority_db.ids["profile"]))
    monkeypatch.setattr(account_routes, "verify_deletion_confirmation", lambda *args, **kwargs: None)
    request = Request({"type": "http", "method": "POST", "path": "/account/delete", "session": {}})
    response = account_routes.delete_account(request, woodchuck_id="fixture", pin="fixture", confirmation="DELETE")
    assert response.status_code == 303
    with authority_db() as session:
        assert session.get(WoodchuckProfile, authority_db.ids["profile"]).status == "deleted"


def test_calendar_bootstrap_commit_rechecks_cutover_clock_and_profile(authority_db, monkeypatch):
    with authority_db() as session:
        control = session.get(PersistentTeamControl, 1)
        control.activated_at = control.rules_from_week_start = None
        session.commit()
    clock = {"at": BOUNDARY - timedelta(seconds=1)}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["at"].astimezone(tz) if tz else clock["at"].replace(tzinfo=None)
    def bootstrap(session, now):
        assert now < BOUNDARY
        # Model ensure_current_contest_data's real internal commit, followed
        # by a cutover winner before the request reacquires its authority.
        season = session.get(Season, authority_db.ids["current"])
        session.commit()
        with authority_db() as applying:
            control = team_authority.lock_authority(applying)
            control.activated_at = BOUNDARY
            control.rules_from_week_start = BOUNDARY.date()
            profile = applying.get(WoodchuckProfile, authority_db.ids["profile"])
            profile.display_name = "Updated after bootstrap"
            applying.commit()
        clock["at"] = BOUNDARY + timedelta(seconds=1)
        return season, None, SimpleNamespace(week_start=BOUNDARY.date())
    monkeypatch.setattr(teams, "datetime", Clock)
    monkeypatch.setattr(teams, "ensure_current_contest_data", bootstrap)
    monkeypatch.setattr(teams, "current_profile", lambda request, session:
                        session.get(WoodchuckProfile, authority_db.ids["profile"]))
    with authority_db() as session:
        request = Request({"type": "http", "method": "POST", "path": "/teams/selection"})
        profile, season, now = teams.authenticated_context(request, session)
        assert profile.display_name == "Updated after bootstrap"
        assert now == BOUNDARY + timedelta(seconds=1)
        team = session.get(Team, authority_db.ids["teams"][0])
        row, _ = teams.select_team(session, profile=profile, season=season, team=team, now=now)
        session.commit()
        assert row.is_persistent and team_authority.utc(row.started_at) == now
