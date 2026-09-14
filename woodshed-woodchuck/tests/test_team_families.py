"""H1A identity, atomic creation and unchanged seasonal/public behavior."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import teams
from app.contests import ensure_current_contest_data, lifetime_team_identity
from app.db import Base
from app.models import (ContestResult, ProfileCapability, Season, Team, TeamFamily,
                        TeamMembership, WoodchuckProfile)
from tests.test_teams import profile

NOW = datetime(2026, 7, 28, 15, tzinfo=timezone.utc)


def disposable_url(tmp_path, backend):
    if backend == "sqlite":
        return f"sqlite:///{tmp_path / 'families.db'}"
    raw = os.getenv("WW_BILLING_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("No explicit disposable PostgreSQL test database configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database == "ww_billing_a2_test"
    schema = "team_family_" + uuid4().hex
    bootstrap = create_engine(url)
    with bootstrap.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    bootstrap.dispose()
    return url.update_query_dict({
        "options": f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=15000"
    }).render_as_string(hide_password=False)


@pytest.fixture(params=["sqlite", "postgresql"])
def family_db(request, tmp_path):
    engine = (create_engine("sqlite://", poolclass=StaticPool) if request.param == "sqlite"
              else create_engine(disposable_url(tmp_path, request.param)))
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        if engine.dialect.name == "sqlite":
            connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        season, _, _ = ensure_current_contest_data(session, now=NOW)
        owner, other = profile(session, 1), profile(session, 2)
        session.add(ProfileCapability(profile_id=owner.id, capability="band_director"))
        session.commit()
        factory.test_ids = (season.id, owner.id, other.id)
    yield factory
    engine.dispose()


def create(session, factory, private=False, other=False, **values):
    season_id, owner_id, other_id = factory.test_ids
    arguments = dict(profile=session.get(WoodchuckProfile, other_id if other else owner_id),
                     season=session.get(Season, season_id), name="Brass Cats",
                     emblem_key="emoji:cat", now=NOW)
    arguments.update(values)
    if private:
        return teams.create_director_team(session, **arguments)
    return teams.create_and_join_team(session, **arguments)[0]


def counts(session):
    return tuple(session.scalar(select(func.count()).select_from(model))
                 for model in (TeamFamily, Team, TeamMembership))


@pytest.mark.parametrize("private", [False, True])
def test_creation_one_family_and_private_payload(family_db, private):
    with family_db() as session:
        team = create(session, family_db, private=private)
        assert counts(session) == (1, 1, 0 if private else 1)
        family = session.get(TeamFamily, team.family_id)
        assert family.created_at is not None
        assert "family_id" not in teams.team_payload(team)
        owner = session.get(WoodchuckProfile, family_db.test_ids[1])
        payload = teams.director_team_payload(session, profile=owner,
                    season=session.get(Season, team.season_id)) if private else teams.selection_payload(
                        session, profile=owner, now=NOW)
        assert "family_id" not in repr(payload)


@pytest.mark.parametrize("private,collision", [(False, "name"), (False, "emblem"),
    (False, "creator"), (True, "name"), (True, "emblem"), (True, "code")])
def test_duplicate_creation_rolls_back_family(family_db, monkeypatch, private, collision):
    with family_db() as session:
        team = create(session, family_db, private=private)
        before = counts(session)
        if collision == "code":
            monkeypatch.setattr(teams, "_new_join_code", lambda session: team.join_code)
        with pytest.raises(ValueError):
            create(session, family_db, private=private, other=not private and collision != "creator",
                   name=" BRASS   CATS " if collision == "name" else "Woodwind Cats",
                   emblem_key="emoji:cat" if collision == "emblem" else "emoji:dog")
        # A later commit cannot accidentally persist the failed family.
        session.commit()
        assert counts(session) == before


def test_membership_failure_rolls_back_all_creation(family_db, monkeypatch):
    with family_db() as session:
        original = teams.select_team
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise ValueError("simulated post-membership failure")
        monkeypatch.setattr(teams, "select_team", fail)
        with pytest.raises(ValueError, match="post-membership"):
            create(session, family_db)
        session.commit()
        assert counts(session) == (0, 0, 0)


def test_no_commit_inside_family_helper(family_db):
    with family_db() as session:
        teams._create_team_with_new_family(session, season_id=family_db.test_ids[0],
            display_name="Rollback", normalized_name="rollback", emblem_key="emoji:cat")
        session.flush()
        session.rollback()
    with family_db() as session:
        assert counts(session) == (0, 0, 0)


@pytest.mark.parametrize("violation", ["null", "missing", "family", "creator", "delete"])
def test_family_and_existing_creator_constraints(family_db, violation):
    with family_db() as session:
        team = create(session, family_db)
        original_family_id = team.family_id
        with pytest.raises(IntegrityError):
            if violation == "delete":
                session.delete(session.get(TeamFamily, team.family_id))
            else:
                family_id = None if violation == "null" else 999999 if violation == "missing" else team.family_id
                if violation == "creator":
                    family = TeamFamily()
                    session.add(family)
                    session.flush()
                    family_id = family.id
                session.add(Team(season_id=team.season_id, family_id=family_id,
                    display_name="Different", normalized_name="different", emblem_key="emoji:dog",
                    creator_profile_id=team.creator_profile_id if violation == "creator" else None))
            session.flush()
        session.rollback()
        assert counts(session) == (1, 1, 1)
        assert session.get(TeamFamily, original_family_id) is not None


def test_cross_season_schema_and_existing_hall_heuristic(family_db):
    with family_db() as session:
        first = create(session, family_db)
        later = Season(key="schema-later", name="Later", starts_on=date(2027, 7, 5), status="planned")
        session.add(later)
        session.flush()
        second = Team(season_id=later.id, family_id=first.family_id, display_name="Renamed",
            normalized_name="renamed", emblem_key="emoji:cat", creator_profile_id=first.creator_profile_id)
        session.add(second)
        session.commit()
        assert counts(session) == (1, 2, 1)
        # H1A does not change the existing creator/name heuristic, even when a
        # test explicitly models a future cross-season family.
        first_result = ContestResult(team_id=first.id, subject_key=str(first.id))
        second_result = ContestResult(team_id=second.id, subject_key=str(second.id))
        assert lifetime_team_identity(first_result, first) != lifetime_team_identity(second_result, second)
        second.normalized_name = first.normalized_name
        separate_family = TeamFamily()
        session.add(separate_family)
        session.flush()
        second.family_id = separate_family.id
        session.commit()
        assert lifetime_team_identity(first_result, first) == lifetime_team_identity(second_result, second)


def test_team_deletion_retains_family(family_db):
    with family_db() as session:
        team = create(session, family_db, private=True)
        family_id = team.family_id
        session.delete(team)
        session.commit()
        assert session.get(TeamFamily, family_id) is not None


def test_postgres_concurrent_duplicate_creation(family_db):
    with family_db() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("Requires PostgreSQL concurrent transactions")
    barrier = Barrier(2)
    def worker():
        with family_db() as session:
            try:
                # H1B serializes from the season fence, before family creation.
                barrier.wait(timeout=10)
                create(session, family_db)
                return "created"
            except ValueError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        assert sorted(f.result(timeout=20) for f in futures) == ["conflict", "created"]
    with family_db() as session:
        assert counts(session) == (1, 1, 1)
