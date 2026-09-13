"""Only disposable databases: migrate membership tables without changing history."""
from pathlib import Path
from datetime import datetime, timezone
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import pytest
from app.db import Base
from app.models import WoodchuckProfile, TrustedVerifier, BillingAccount
from app import memberships as m

NEW_TABLES = {"billing_accounts", "memberships", "membership_seats", "membership_seat_invitations",
              "provider_subscriptions", "billing_provider_events", "membership_audit_events"}


def test_membership_migration_roundtrip_and_history(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "n4i5j6k7l8m9")
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(WoodchuckProfile(id=1, woodchuck_id="WC-KEEPME", display_name="History",
            pin_hash="unchanged", instrument="Trumpet", level="Beginner", goal="Practice"))
        session.add(TrustedVerifier(id=1, display_name="Adult", email="adult@example.test", pin_hash="same"))
        session.commit()
    old_tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    def snapshot():
        with engine.connect() as conn:
            return {table: [tuple(row) for row in conn.execute(text(f'SELECT * FROM "{table}"'))]
                    for table in old_tables}
    before = snapshot()
    command.upgrade(config, "o5j6k7l8m9n0")
    assert NEW_TABLES <= set(inspect(engine).get_table_names())
    assert snapshot() == before
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_object": lambda obj, name, type_, reflected, compare_to:
            name in NEW_TABLES if type_ == "table" else True})
        differences = compare_metadata(context, Base.metadata)
        assert differences == []
    with Session(engine) as session:
        member = m.create_complimentary_membership(session, m.Actor("student", 1), m.Actor("admin"))
        session.commit()
        assert m.student_has_full_access(session, 1)
        session.add(BillingAccount(profile_id=1, verifier_id=1))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    assert snapshot() == before
    command.downgrade(config, "n4i5j6k7l8m9")
    assert not NEW_TABLES & set(inspect(engine).get_table_names())
    assert snapshot() == before
    command.upgrade(config, "o5j6k7l8m9n0")
    assert snapshot() == before
    engine.dispose()
