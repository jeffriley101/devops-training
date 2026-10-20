"""WL2-01 actual c24 upgrades, preserved history and durable-play constraints.

PostgreSQL requires an explicitly supplied private, disposable Unix-socket cluster.
No fixture uses create_all, stamps a revision, or touches an application database.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from queue import Queue
from threading import Event
import time
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData, Table, create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from app import models as m
from app import classroom_models as cm
from app.db import Base
from app.weekly_models import WeeklyWheelPlay

APP = Path(__file__).resolve().parents[1]
OLD, NEW = "c24class001", "w25weekly001"
NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def config():
    return Config(str(APP / "alembic.ini"))


@pytest.fixture(params=["sqlite", "postgresql"])
def db(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        path = tmp_path / "weekly2.db"
        assert not path.exists()
        url = f"sqlite:///{path}"
    else:
        raw = os.getenv("WW_WEEKLY2_TEST_POSTGRES_URL")
        if not raw:
            pytest.skip("Explicit disposable Weekly2 PostgreSQL socket URL required")
        parsed = make_url(raw)
        assert parsed.drivername == "postgresql+psycopg" and not parsed.host
        assert parsed.database.startswith("ww_weekly2_")
        assert set(parsed.query) == {"host", "port"}
        socket = Path(parsed.query["host"])
        assert socket.parent == Path("/tmp") and socket.name.startswith("wl2pg-")
        assert socket.resolve(strict=True) == socket and socket.stat().st_uid == os.getuid()
        assert socket.stat().st_mode & 0o777 == 0o700
        control = create_engine(parsed)
        schema = "wl2_" + uuid4().hex
        with control.begin() as c:
            assert c.scalar(text("SELECT inet_server_addr()")) is None
            assert c.scalar(text("SHOW listen_addresses")) == ""
            assert c.scalar(text("SELECT current_database()")) == parsed.database
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        control.dispose()
        url = parsed.update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=20000"}).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url)
    def sqlite_fk(connection, _):
        if engine.dialect.name == "sqlite":
            connection.execute("PRAGMA foreign_keys=ON")
    event.listen(engine, "connect", sqlite_fk)
    try:
        yield engine
    finally:
        engine.dispose()  # Preserve disposable files/schemas for failure review.


def revision(engine):
    with engine.connect() as c:
        return c.scalar(text("SELECT version_num FROM alembic_version"))


def rows(engine, *, old_only=False):
    with engine.connect() as c:
        md = MetaData()
        result = {}
        for name in sorted(inspect(c).get_table_names()):
            if name == "alembic_version" or (old_only and name == "weekly_wheel_plays"):
                continue
            table = Table(name, md, autoload_with=c, resolve_fks=False)
            cols = [col for col in table.c if not (old_only and name == "woodchuck_profiles" and col.name == "first_choice_instrument")]
            result[name] = [dict(row) for row in c.execute(select(*cols).order_by(*table.primary_key.columns)).mappings()]
        return result


def schema_contract(engine):
    """Full old-table structure and enforcement triggers, beyond ORM metadata."""
    def stable(value):
        # Each inspection creates new predicate objects; compare their exact SQL,
        # not object identity. Keep every partial-index predicate in the contract.
        if isinstance(value, TextClause):
            return str(value)
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    with engine.connect() as c:
        inspector = inspect(c)
        tables = {}
        for name in sorted(inspector.get_table_names()):
            if name in {"alembic_version", "weekly_wheel_plays"}:
                continue
            columns = [{**col, "type": str(col["type"])} for col in inspector.get_columns(name)
                       if not (name == "woodchuck_profiles" and col["name"] == "first_choice_instrument")]
            tables[name] = dict(columns=columns, pk=inspector.get_pk_constraint(name),
                foreign_keys=inspector.get_foreign_keys(name), indexes=inspector.get_indexes(name),
                unique=inspector.get_unique_constraints(name), checks=inspector.get_check_constraints(name))
        if engine.dialect.name == "sqlite":
            triggers = list(c.execute(text("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name")))
        else:
            triggers = list(c.execute(text("SELECT t.tgname, pg_get_triggerdef(t.oid), pg_get_functiondef(t.tgfoid) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal ORDER BY t.tgname")))
        return stable(tables), triggers


def profile(engine, ident):
    # Reflect c24 rather than issuing an INSERT with the future nullable column.
    with engine.begin() as c:
        table = Table("woodchuck_profiles", MetaData(), autoload_with=c)
        c.execute(table.insert().values(id=ident, woodchuck_id=f"WL2-{ident}",
            display_name=f"Synthetic {ident}", pin_hash="synthetic-not-a-login",
            instrument="Trumpet", level="Beginner", goal="Practice",
            created_at=NOW, updated_at=NOW))


def seed_history(engine):
    """Valid synthetic storage records; no runtime permissions are granted."""
    profile(engine, 1)
    with Session(engine) as s:
        s.add(m.WoodchuckState(profile_id=1, state_json={"dandelions": 123, "xp": 456}, revision=7))
        s.add(m.Season(id=1, key="wl2-history", name="Synthetic history", starts_on=date(2026, 9, 1), status="closed"))
        s.add(m.TeamFamily(id=1))
        s.add(m.TrustedVerifier(id=1, email="wl2@example.invalid", display_name="Synthetic Adult", pin_hash="synthetic"))
        s.flush()
        s.add(m.Organization(id=1, name="Synthetic Program", organization_type="music", created_by_verifier_id=1))
        s.add(m.Team(id=1, season_id=1, family_id=1, display_name="Synthetic Team", normalized_name="synthetic team", emblem_key="synthetic"))
        s.add(m.Contest(id=1, key="wl2-history", name="Synthetic contest", metric_type="practice_minutes", subject_type="student"))
        s.add(m.ContestWeek(id=1, season_id=1, week_start=date(2026, 10, 5), week_end=date(2026, 10, 11),
            verification_deadline_at=NOW, finalize_after=NOW, status="finalized", finalized_at=NOW,
            finalizer_rules_version="contest_finalizer_v2", practice_scoring_mode="submitted_v2", team_roster_frozen_at=NOW))
        s.flush()
        s.add(cm.ClassroomProgram(organization_id=1, owner_verifier_id=1))
        s.add(m.ContestResult(id=1, contest_week_id=1, contest_id=1, division="open", subject_type="student",
            subject_key="1", profile_id=1, display_name_snapshot="Synthetic historical name", score=30, precise_score=30.5, rank=1, medal="gold"))
        s.add(m.TeamWeekMembershipSnapshot(contest_week_id=1, profile_id=1, team_id=1, snapshot_at=NOW))
        s.flush()
        s.add(m.RewardGrant(profile_id=1, contest_result_id=1, source_key="synthetic-history", reward_type="dandelions", amount=123))
        s.add(cm.ClassroomClass(id=1, program_id=1, display_name="Synthetic Class"))
        s.add(cm.ClassroomEntitlement(id=1, program_id=1, source="trial", status="active", starts_at=NOW,
            ends_at=NOW+timedelta(days=14), class_limit=10, teacher_limit=2,
            approved_by_verifier_id=1, provenance="ordinary_trial"))
        s.flush()
        s.add(cm.ClassroomStudentMembership(id=1, class_id=1, profile_id=1))
        s.flush()
        s.add(cm.ClassroomMembershipPeriod(id=1, membership_id=1, starts_at=NOW, state="active"))
        s.flush()
        s.add(cm.ClassroomReportingPeriod(id=1, program_id=1, class_id=1, membership_id=1, membership_period_id=1,
            profile_id=1, authorizer_profile_id=1, account_declared_at=NOW, entitlement_id=1,
            class_activation_at=NOW, scope_version="classroom-boundary-v1", notice_version="classroom-boundary-notice-v1", starts_at=NOW))
        s.flush()
        s.add(cm.ClassroomS3AuditEvent(program_id=1, class_id=1, profile_id=1, period_id=1, actor_profile_id=1,
            action="reporting_granted", scope_version="classroom-boundary-v1", notice_version="classroom-boundary-notice-v1"))
        s.commit()


def play(**changes):
    values = dict(id=uuid4().hex, profile_id=1, request_key=uuid4().hex, funded_day=NOW.date(),
        funded_at=NOW, daily_ordinal=1, outcomes=[], completed_at=None)
    values.update(changes)
    return values


def insert_play(engine, **changes):
    with engine.begin() as c:
        c.execute(WeeklyWheelPlay.__table__.insert().values(**play(**changes)))


def test_one_additive_head_follows_c24():
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == [NEW]
    assert script.get_revision(NEW).down_revision == OLD
    assert len(list(script.walk_revisions())) == 63


def test_empty_database_upgrade(db):
    command.upgrade(config(), "head")
    assert revision(db) == NEW
    with db.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM weekly_wheel_plays")) == 0
        assert c.scalar(text("SELECT count(*) FROM woodchuck_profiles")) == 0
        assert c.scalar(text("SELECT count(*) FROM reward_grants")) == 0


def test_populated_c24_upgrade_preserves_authority_history_and_balances(db):
    command.upgrade(config(), OLD)
    seed_history(db)
    before, structure = rows(db, old_only=True), schema_contract(db)
    command.upgrade(config(), NEW)
    assert revision(db) == NEW
    assert rows(db, old_only=True) == before
    assert schema_contract(db) == structure
    with db.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM weekly_wheel_plays")) == 0
        assert c.scalar(text("SELECT first_choice_instrument FROM woodchuck_profiles WHERE id=1")) is None
        if db.dialect.name == "sqlite":
            assert list(c.execute(text("PRAGMA foreign_key_check"))) == []
    # Re-running upgrade is a no-op, including saved preference and old instrument.
    with db.begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET first_choice_instrument='Flute' WHERE id=1"))
    saved = rows(db)
    command.upgrade(config(), NEW)
    assert rows(db) == saved


def test_legacy_first_choice_is_null_without_grants_or_activation(db):
    command.upgrade(config(), OLD)
    profile(db, 1)
    before = rows(db, old_only=True)
    command.upgrade(config(), NEW)
    with Session(db) as s:
        account = s.get(m.WoodchuckProfile, 1)
        assert account.first_choice_instrument is None and account.instrument == "Trumpet"
        assert account.status == "active" and account.session_version == 0
    with db.begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET first_choice_instrument='Flute' WHERE id=1"))
    assert rows(db, old_only=True) == before
    profile(db, 2)
    with Session(db) as s:
        assert s.get(m.WoodchuckProfile, 2).first_choice_instrument is None
    with db.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM reward_grants")) == 0
        assert c.scalar(text("SELECT activated_at FROM persistent_team_control")) is None


def test_wheel_request_slot_ordinal_and_foreign_key_constraints(db):
    command.upgrade(config(), NEW)
    profile(db, 1)
    profile(db, 2)
    insert_play(db, id="first", request_key="request", outcomes=[{"kind": "stored-synthetic", "value": 1}])
    invalid = [dict(id="first", daily_ordinal=2), dict(request_key="request", daily_ordinal=2),
        dict(daily_ordinal=1), dict(profile_id=999), dict(daily_ordinal=0), dict(daily_ordinal=10),
        dict(profile_id=None), dict(request_key=None), dict(funded_at=None), dict(funded_day=None),
        dict(daily_ordinal=None), dict(outcomes=text("NULL"))]
    for changes in invalid:
        before = rows(db)
        with pytest.raises(IntegrityError):
            insert_play(db, **{"daily_ordinal": 2, **changes})
        assert rows(db) == before
    insert_play(db, request_key="other-day", funded_day=NOW.date()+timedelta(days=1))
    insert_play(db, profile_id=2, request_key="request")
    insert_play(db, daily_ordinal=9)
    with Session(db) as s:
        row = s.get(WeeklyWheelPlay, "first")
        assert row.outcomes == [{"kind": "stored-synthetic", "value": 1}] and row.completed_at is None
        row.completed_at = NOW + timedelta(seconds=1)
        s.commit()
    with db.begin() as c:
        c.execute(text("DELETE FROM woodchuck_profiles WHERE id=2"))
        assert c.scalar(text("SELECT count(*) FROM weekly_wheel_plays WHERE profile_id=2")) == 0
        assert c.scalar(text("SELECT count(*) FROM weekly_wheel_plays WHERE profile_id=1")) == 3
        assert c.scalar(text("SELECT count(*) FROM reward_grants")) == 0


def test_metadata_matches_actual_new_schema(db):
    command.upgrade(config(), NEW)
    with db.connect() as c:
        assert compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata) == []
        inspector = inspect(c)
        cols = {col["name"]: col for col in inspector.get_columns("woodchuck_profiles")}
        assert cols["first_choice_instrument"]["nullable"] and cols["first_choice_instrument"]["default"] is None
        assert {x["name"] for x in inspector.get_unique_constraints("weekly_wheel_plays")} == {"uq_weekly_wheel_request", "uq_weekly_wheel_day_slot"}
        assert inspector.get_pk_constraint("weekly_wheel_plays")["constrained_columns"] == ["id"]
        fk, = inspector.get_foreign_keys("weekly_wheel_plays")
        assert fk["referred_table"] == "woodchuck_profiles" and fk["options"]["ondelete"] == "CASCADE"
        assert {x["name"] for x in inspector.get_check_constraints("weekly_wheel_plays")} == {"ck_weekly_wheel_daily_ordinal"}
        assert any(x["name"] == "ix_weekly_wheel_plays_profile_id" for x in inspector.get_indexes("weekly_wheel_plays"))


def test_empty_round_trip(db):
    command.upgrade(config(), OLD)
    profile(db, 1)
    before, structure = rows(db), schema_contract(db)
    command.upgrade(config(), NEW)
    command.downgrade(config(), OLD)
    assert revision(db) == OLD and rows(db) == before and schema_contract(db) == structure
    command.upgrade(config(), NEW)
    assert revision(db) == NEW and rows(db, old_only=True) == before


@pytest.mark.parametrize("used", ["preference", "play"])
def test_used_history_refusal(db, used):
    command.upgrade(config(), NEW)
    profile(db, 1)
    if used == "preference":
        with db.begin() as c:
            c.execute(text("UPDATE woodchuck_profiles SET first_choice_instrument='Flute' WHERE id=1"))
    else:
        insert_play(db)
    before = rows(db)
    with pytest.raises(RuntimeError, match="weekly_foundation_in_use"):
        command.downgrade(config(), OLD)
    assert revision(db) == NEW and rows(db) == before


def test_operator_refuses_unapproved_weekly_head_without_mutation(db):
    from app.contest_week_provisioning import schema_guard
    from app.team_continuity_inventory import InventoryError
    command.upgrade(config(), OLD)
    with db.connect() as c:
        schema_guard(c)
    command.upgrade(config(), NEW)
    before = rows(db)
    with db.connect() as c, pytest.raises(InventoryError, match="revision_not_approved"):
        schema_guard(c)
    assert rows(db) == before


@pytest.mark.parametrize("db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("collision", ["request", "day_slot"])
def test_postgres_duplicate_insert_waits_then_refuses(db, collision, record_property):
    command.upgrade(config(), NEW)
    profile(db, 1)
    ready, release, pids = Event(), Event(), Queue()
    def insert(first):
        with db.connect() as c:
            assert c.get_isolation_level() == "READ COMMITTED"
            pids.put((first, c.scalar(text("SELECT pg_backend_pid()"))))
            try:
                c.execute(WeeklyWheelPlay.__table__.insert().values(**play(
                    request_key="same" if collision == "request" else str(first),
                    daily_ordinal=(1 if first else 2) if collision == "request" else 1)))
                if first:
                    ready.set()
                    assert release.wait(12)
                c.commit()
                return "committed"
            except IntegrityError:
                c.rollback()
                return "duplicate_refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(insert, True)
        assert ready.wait(10)
        second = pool.submit(insert, False)
        observed = dict(pids.get(timeout=10) for _ in range(2))
        blocking = []
        try:
            deadline = time.monotonic() + 8
            with db.connect() as c:
                while time.monotonic() < deadline:
                    blockers = c.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": observed[False]})
                    if observed[True] in blockers:
                        blocking = blockers
                        break
                    time.sleep(0.01)
            assert observed[True] in blocking, "No actual unique-constraint lock wait observed"
            record_property("postgres_blocking", json.dumps({"collision": collision, "pids": observed, "blocking": blocking, "isolation": "READ COMMITTED"}))
        finally:
            release.set()
        assert (first.result(timeout=15), second.result(timeout=15)) == ("committed", "duplicate_refused")
    with db.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM weekly_wheel_plays")) == 1
        assert c.scalar(text("SELECT count(*) FROM reward_grants")) == 0
