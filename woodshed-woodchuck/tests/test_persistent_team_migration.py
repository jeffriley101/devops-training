"""Additive migration preserves rows and refuses rollback after authority use."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, text, event, MetaData, Table, select
from sqlalchemy.engine import Engine

from app.db import Base
from tests.test_team_families import disposable_url


@pytest.fixture(autouse=True)
def disposable_sqlite_configuration():
    # Exercise batch migration safety even when the existing connection
    # enforces foreign keys. Faster fsync is only for disposable test files.
    def configure(connection, _):
        if type(connection).__module__ == "sqlite3":
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA foreign_keys=ON")
    event.listen(Engine, "connect", configure)
    yield
    event.remove(Engine, "connect", configure)


def historical_snapshot(connection):
    from app.persistent_team_cutover import TABLES, normalized
    metadata = MetaData()
    result = {}
    for name in TABLES:
        if name in {"persistent_team_control", "team_membership_transitions"}:
            continue
        table = Table(name, metadata, autoload_with=connection, resolve_fks=False)
        columns = [c for c in table.c if c.name not in {"is_persistent", "is_operating", "team_membership_rules_version"}]
        result[name] = normalized([dict(r) for r in connection.execute(select(*columns).order_by(*table.primary_key.columns)).mappings()])
    return result


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_populated_history_upgrade_is_exactly_preserved(tmp_path, monkeypatch, backend):
    from tests.test_persistent_team_cutover import seed
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = seed(url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "f19arcade001")
    with engine.connect() as c:
        before = historical_snapshot(c)
        assert len(before["teams"]) == 10
        assert len(before["team_memberships"]) == 40
        assert len(before["contest_results"]) == len(before["team_week_membership_snapshots"]) == 1
    command.upgrade(config, "p20team001")
    with engine.connect() as c:
        assert historical_snapshot(c) == before
        if backend == "sqlite":
            assert list(c.execute(text("PRAGMA foreign_key_check"))) == []
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
    engine.dispose()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_upgrade_downgrade_reupgrade_no_automatic_promotion(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "f19arcade001")
    engine = create_engine(url)
    command.upgrade(config, "p20team001")
    with engine.connect() as c:
        assert c.execute(text("SELECT id, activated_at, rules_from_week_start FROM persistent_team_control")).one() == (1, None, None)
        assert c.scalar(text("SELECT count(*) FROM teams WHERE is_operating=true")) == 0
        assert c.scalar(text("SELECT count(*) FROM team_memberships WHERE is_persistent=true")) == 0
        assert c.scalar(text("SELECT count(*) FROM contest_weeks WHERE team_membership_rules_version <> 'legacy_seasonal_v1'")) == 0
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
    command.downgrade(config, "f19arcade001")
    command.upgrade(config, "p20team001")
    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
    engine.dispose()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_downgrade_refuses_activated_control(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "p20team001")
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("UPDATE persistent_team_control SET activated_at=CURRENT_TIMESTAMP, rules_from_week_start='2026-10-05' WHERE id=1"))
    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(config, "f19arcade001")
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "p20team001"
    engine.dispose()
