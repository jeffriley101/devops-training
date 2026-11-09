"""S4 on the actual C36 schema; revocation after an observed PostgreSQL wait."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sys
import time

from alembic import command
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app import classroom_guest as guest, classroom_s2 as s2, main
from app.db import Base
from app.operator_schema_compatibility import installed_revision
from test_classroom_guest_s4 import s4_web, classroom_db, adult_pin_hash, enter, check, fingerprints
from test_classroom_progress_migration import migrated_db
from tests.test_weekly2_foundation_migration import config


@pytest.fixture
def pg_s4(s4_web, migrated_db, monkeypatch):
    command.upgrade(config(), 'c36choice001')  # Disposable schema setup only.
    with s4_web.factory() as source, migrated_db.begin() as target:
        for table in Base.metadata.sorted_tables:
            data = [dict(r._mapping) for r in source.execute(select(table))]
            for row in data:
                for column in table.columns:
                    value = row.get(column.name)
                    if isinstance(value, datetime) and value.tzinfo is None and getattr(column.type, 'timezone', False):
                        row[column.name] = value.replace(tzinfo=timezone.utc)
            if data: target.execute(table.insert(), data)
    factory = sessionmaker(migrated_db, expire_on_commit=False)
    for name, module in tuple(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    s4_web.factory = factory
    return s4_web


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
def test_c36_schema_and_all_records_unchanged_by_s4(pg_s4):
    refs = pg_s4; before = fingerprints(refs.factory)
    with refs.factory.kw['bind'].connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'c36choice001'
    client = TestClient(main.app); result = enter(client)
    assert result.status_code == 200, result.text
    binding = result.json()['binding']
    assert check(client, binding).status_code == 200
    assert check(client, binding, 'leave').status_code == 200
    assert fingerprints(refs.factory) == before
    with refs.factory.kw['bind'].connect() as connection:
        assert installed_revision(connection, RuntimeError) == 'c36choice001'


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('loss', ['rotation', 'expiry'])
def test_current_authority_after_observed_program_lock_wait(pg_s4, loss):
    refs = pg_s4; client = TestClient(main.app); binding = enter(client).json()['binding']
    db = refs.factory.kw['bind']
    with db.connect() as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = blocker.begin()
        blocker.execute(text('SELECT organization_id FROM classroom_programs WHERE organization_id=1 FOR UPDATE'))
        response = pool.submit(check, client, binding)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with db.connect() as observer:
                    waiting = observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%classroom_programs%'"))
                if waiting: break
                time.sleep(.02)
            else: pytest.fail('Guest authority read did not encounter the held Program lock')
            if loss == 'rotation':
                blocker.execute(text('UPDATE classroom_entry_codes SET is_current=false, revoked_at=:at, revoked_by_verifier_id=1 WHERE id=:id'),
                                {'at': s2._now(), 'id': refs.code})
            else:
                refs.clock[0] += 900
            transaction.commit()
            before = fingerprints(refs.factory)
            assert response.result(timeout=10).status_code == 403
            assert fingerprints(refs.factory) == before
        finally:
            if transaction.is_active: transaction.rollback()
