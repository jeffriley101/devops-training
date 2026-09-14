"""Opt-in, isolated PostgreSQL transactions; no production URL is accepted."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, local

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app import teams, team_continuity, contest_admin, account_deletion
from app.db import Base
from app.models import Season, Team, TeamMembership, TeamJoinRequest, WoodchuckProfile, ProfileCapability
from tests.test_team_families import disposable_url
from tests.test_team_continuity import seed, source_team, dest_team, run, count, add_member, BOUNDARY


@pytest.fixture
def pg(tmp_path):
    engine = create_engine(disposable_url(tmp_path, "postgresql"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    seed(factory)
    yield factory
    engine.dispose()


def ordered_race(monkeypatch, first, second):
    """Both workers reach the real lock; hold the first until the second enters."""
    state = local()
    acquired, entering = Event(), Event()
    original = team_continuity.lock_team_seasons
    def fence(session, *ids):
        if getattr(state, "visited", False):
            return original(session, *ids)
        state.visited = True
        if state.role == "second":
            entering.set()
        result = original(session, *ids)
        if state.role == "first":
            acquired.set()
            assert entering.wait(5), "competing writer did not enter"
        return result
    for module in (teams, team_continuity, contest_admin, account_deletion):
        monkeypatch.setattr(module, "lock_team_seasons", fence)
    def worker(role, function):
        state.role = role
        return function()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(worker, "first", first)
        assert acquired.wait(5), "first writer did not acquire season lock"
        b = pool.submit(worker, "second", second)
        return a.result(timeout=15), b.result(timeout=15)


@pytest.mark.parametrize("competitor", ["continuation", "creation", "switch", "approval", "moderation", "deletion"])
@pytest.mark.parametrize("continuation_first", [True, False])
def test_continuation_races(pg, monkeypatch, competitor, continuation_first):
    with pg() as s:
        old = source_team(s, pg)
        target = dest_team(s, pg)
        if competitor == "approval":
            target.visibility = "private"; target.director_led = True
            target.creator_profile_id = pg.ids[4]; target.join_code = "RACECODE"
            s.add(ProfileCapability(profile_id=pg.ids[4], capability="band_director"))
            request_row = TeamJoinRequest(team_id=target.id, season_id=target.season_id,
                profile_id=pg.ids[2], status="pending")
            s.add(request_row); s.flush()
            request_id = request_row.id
        s.commit()
        old_id, target_id = old.id, target.id
    monkeypatch.setattr(teams, "SessionLocal", pg)
    monkeypatch.setattr(contest_admin, "SessionLocal", pg)
    monkeypatch.setenv("CONTEST_ADMIN_TOKEN", "test-continuity-admin")

    def continuation():
        with pg() as s:
            outcome = run(s, pg); s.commit()
            return outcome

    def competing():
        if competitor == "continuation":
            return continuation()
        if competitor == "moderation":
            request = Request({"type": "http", "headers": [
                (b"x-contest-admin-token", b"test-continuity-admin")], "session": {}})
            return contest_admin.moderate_team(old_id, request, "hidden")
        with pg() as s:
            student = s.get(WoodchuckProfile, pg.ids[2])
            dest = s.get(Season, pg.ids[1])
            if competitor == "deletion":
                account_deletion.anonymize_woodchuck_account(s, profile=student, now=BOUNDARY)
                s.commit()
                return
            if competitor == "creation":
                try:
                    teams.create_and_join_team(s, profile=student, season=dest, name="New Choice",
                        emblem_key="letter:X", now=BOUNDARY)
                except ValueError:
                    s.rollback()
                return
            if competitor == "switch":
                teams.select_team(s, profile=student, season=dest, team=s.get(Team, target_id), now=BOUNDARY)
                s.commit()
                return
        # Use the real route's auth/decision path with the established session
        # helper format. Freeze only the request clock/bootstrap return: all
        # authorization and private owner/request validation remain real.
        request = Request({"type": "http", "headers": [], "session": {"woodchuck_profile_id": pg.ids[4]}})
        return teams.resolve_private_team_request(target_id, request_id, request,
                                                 teams.JoinRequestDecision(action="approve"))

    if competitor == "approval":
        # authenticated_context normally bootstraps the calendar and uses wall
        # clock time; retain current_profile authentication but pin the season.
        def context(request, session, now=None):
            profile = teams.current_profile(request, session)
            assert profile is not None
            return profile, session.get(Season, pg.ids[1]), BOUNDARY
        monkeypatch.setattr(teams, "authenticated_context", context)
    if continuation_first:
        ordered_race(monkeypatch, continuation, competing)
    else:
        ordered_race(monkeypatch, competing, continuation)
    with pg() as s:
        successors = list(s.scalars(select(Team).where(
            Team.family_id == s.get(Team, old_id).family_id, Team.season_id == pg.ids[1])))
        assert len(successors) <= 1
        history = list(s.scalars(select(TeamMembership).where(
            TeamMembership.season_id == pg.ids[1], TeamMembership.profile_id == pg.ids[2])))
        assert sum(m.ended_at is None for m in history) <= 1
        if competitor == "continuation":
            assert len(successors) == len(history) == 1
        elif competitor == "moderation":
            assert s.get(Team, old_id).moderation_status == "hidden"
            assert len(successors) == (1 if continuation_first else 0)
        elif competitor in {"switch", "approval"}:
            assert next(m for m in history if m.ended_at is None).team_id == target_id
            assert len(history) == (2 if continuation_first else 1)
            assert len(successors) == (1 if continuation_first else 0)
        elif competitor == "creation":
            assert len(history) == 1
            assert len(successors) == (1 if continuation_first else 0)
        elif competitor == "deletion":
            assert s.get(WoodchuckProfile, pg.ids[2]).status == "deleted"
            assert all(m.ended_at is not None for m in history)
            assert len(successors) == (1 if continuation_first else 0)
        # Nothing ended or rewrote the historical source membership.
        old_member = s.scalar(select(TeamMembership).where(TeamMembership.team_id == old_id))
        assert (old_member.ended_at is None) == (competitor != "deletion")
