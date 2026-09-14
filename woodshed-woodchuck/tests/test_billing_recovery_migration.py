"""Recovery audit evolution and lossless downgrade guards on disposable SQLite."""
from pathlib import Path
import os
from uuid import uuid4
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text, inspect, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
import pytest
from app.db import Base


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_recovery_audit_migration(tmp_path, monkeypatch, backend):
    url = f"sqlite:///{tmp_path / 'recovery.db'}"
    if backend == "postgresql":
        raw = os.getenv("WW_BILLING_TEST_POSTGRES_URL")
        if not raw:
            pytest.skip("No explicit disposable PostgreSQL test database configured")
        parsed = make_url(raw)
        assert parsed.get_backend_name() == "postgresql" and parsed.host in {"localhost", "127.0.0.1", "::1"}
        assert parsed.database == "ww_billing_a2_test"
        schema = "migration_" + uuid4().hex
        bootstrap = create_engine(parsed)
        with bootstrap.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        bootstrap.dispose()
        url = parsed.update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "p6k7l8m9n0o1")
    engine = create_engine(url)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        if backend == "sqlite":
            connection.execute("PRAGMA foreign_keys=ON")
    # Reuse a real prior-revision fixture; audits are inserted through reflection.
    from sqlalchemy.orm import Session
    from app.models import WoodchuckProfile
    from app import memberships as m
    from tests.test_membership_migration import historical_auditor
    with monkeypatch.context() as patch:
        patch.setattr(m, "audit", historical_auditor(engine))
        with Session(engine) as s:
            s.add(WoodchuckProfile(id=1, woodchuck_id="WC-HISTORY", display_name="History",
                pin_hash="same", instrument="Trumpet", level="Beginner", goal="Practice"))
            s.flush()
            member = m.create_complimentary_membership(s, m.Actor("student", 1), m.Actor("admin"))
            s.commit()
    with engine.connect() as conn:
        old = conn.execute(text("SELECT * FROM membership_audit_events ORDER BY id")).all()
    command.upgrade(config, "q7l8m9n0o1p2")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"include_object": lambda obj, name, type_, reflected, compare_to:
            name == "membership_audit_events" if type_ == "table" else True})
        assert compare_metadata(ctx, Base.metadata) == []
        assert [tuple(r)[:-1] for r in conn.execute(text("SELECT * FROM membership_audit_events ORDER BY id"))] == [tuple(r) for r in old]
    command.downgrade(config, "p6k7l8m9n0o1")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT * FROM membership_audit_events ORDER BY id")).all() == old
    command.upgrade(config, "q7l8m9n0o1p2")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO billing_provider_events (id, provider, external_event_id, received_at, status, payload_hash) "
                          "VALUES (1, 'stripe', 'recorded', CURRENT_TIMESTAMP, 'received', 'hash')"))
        conn.execute(text("INSERT INTO membership_audit_events (billing_event_id, action, actor_type, details, created_at) "
                          "VALUES (1, 'billing_retry_requested', 'admin', '{}', CURRENT_TIMESTAMP)"))
    for statement in (
        "DELETE FROM billing_provider_events WHERE id = 1",
        "DELETE FROM memberships WHERE id = 1",
        "INSERT INTO membership_audit_events (action, actor_type, details, created_at) VALUES ('bad', 'admin', '{}', CURRENT_TIMESTAMP)",
        "INSERT INTO membership_audit_events (billing_event_id, action, actor_type, details, created_at) VALUES (999, 'bad', 'admin', '{}', CURRENT_TIMESTAMP)",
    ):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(statement))
    with pytest.raises(RuntimeError, match="audit history"):
        command.downgrade(config, "p6k7l8m9n0o1")
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT COUNT(*) FROM membership_audit_events WHERE billing_event_id = 1")) == 1
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "q7l8m9n0o1p2"
    assert all(fk["options"].get("ondelete") == "RESTRICT" for fk in inspect(engine).get_foreign_keys("membership_audit_events"))
    engine.dispose()
