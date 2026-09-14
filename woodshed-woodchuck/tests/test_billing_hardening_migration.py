"""A1 successor migration, disposable SQLite only, including retained history."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import pytest
from app.db import Base
from app.models import WoodchuckProfile, CheckoutAttempt
from app import memberships as m

TABLES = {"checkout_attempts", "billing_event_applications", "billing_payment_effects"}


def test_a1_upgrade_downgrade_preserves_old_tables_and_matches_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'a1.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "o5j6k7l8m9n0")
    engine = create_engine(url)
    with Session(engine) as s:
        s.add(WoodchuckProfile(id=1, woodchuck_id="WC-HISTORY", display_name="Retain",
            pin_hash="unchanged", instrument="Trumpet", level="Beginner", goal="Practice"))
        s.flush()
        membership = m.create_complimentary_membership(s, m.Actor("student", 1), m.Actor("admin"))
        s.commit()
        account_id = membership.billing_account_id
    old_tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    def snapshot():
        with engine.connect() as conn:
            return {table: [tuple(row) for row in conn.execute(text(f'SELECT * FROM "{table}"'))]
                    for table in old_tables}
    before = snapshot()
    command.upgrade(config, "p6k7l8m9n0o1")
    assert TABLES <= set(inspect(engine).get_table_names())
    assert snapshot() == before
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"include_object":
            lambda obj, name, type_, reflected, compare_to: name in TABLES if type_ == "table" else True})
        assert compare_metadata(context, Base.metadata) == []
    at = datetime.now(timezone.utc)
    with Session(engine) as s:
        row = CheckoutAttempt(reference="opaque-reference", billing_account_id=account_id,
            provider="stripe", plan_code="full_annual_49", amount_cents=4900, currency="USD",
            interval="year", status="pending", created_at=at, updated_at=at, expires_at=at + timedelta(minutes=1))
        s.add(row)
        s.commit()
        s.expire_all()
        assert s.get(CheckoutAttempt, row.id).amount_cents == 4900
        row.expires_at = at - timedelta(seconds=1)
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
    command.downgrade(config, "o5j6k7l8m9n0")
    assert not TABLES & set(inspect(engine).get_table_names())
    assert snapshot() == before
    command.upgrade(config, "p6k7l8m9n0o1")
    assert snapshot() == before
    engine.dispose()
