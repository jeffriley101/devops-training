"""Real additive migration, evidence preservation and operator fail-closed safeguards."""
import os
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import classroom, classroom_s2 as s2, classroom_reporting as boundary
from app import classroom_progress_authorization as progress
from app.age_models import AccountPrivacy
from app.classroom_models import ClassroomProgressAuthorization as Authorization, ClassroomProgressRecipient, ClassroomS2AuditEvent
from app.db import Base
from app.models import Organization, TrustedVerifier, WoodchuckProfile
from app.operator_classroom_progress_contract import C29
from app.operator_schema_compatibility import installed_revision, classroom_snapshot, assert_classroom_unchanged
from test_classroom_s1 import adult_pin_hash, actor, provision
from test_classroom_s2 import NOW, student
from test_classroom_s3_postgres import setup_reporting
from test_classroom_progress_authorization import accept
from tests.test_weekly2_foundation_migration import config, rows, schema_contract, revision


@pytest.fixture(params=["sqlite", "postgresql"])
def migrated_db(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        directory = Path(mkdtemp(prefix="classroom-s1-progress-migration-", dir="/tmp"))
        url = f"sqlite:///{directory / 'progress.db'}"
    else:
        raw = os.getenv("WW_CLASSROOM_TEST_POSTGRES_URL")
        if not raw:
            pytest.skip("Explicit disposable loopback Classroom PostgreSQL URL required")
        parsed = make_url(raw)
        assert parsed.get_backend_name() == "postgresql" and parsed.host in {"127.0.0.1", "::1"}
        assert parsed.database.startswith("classroom_s1_") and not parsed.query
        schema = "progress_migration_" + uuid4().hex
        control = create_engine(parsed)
        with control.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        control.dispose()
        url = parsed.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url)
    if request.param == "sqlite":
        event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    yield engine
    engine.dispose()


def seeded_sources(db, monkeypatch, adult_pin_hash):
    for gate in ("CLASSROOM_S1_ENABLED", "CLASSROOM_S1_LOCAL_PROVISIONING", "CLASSROOM_S2_ENABLED",
                 "CLASSROOM_S3_ENABLED", "CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED"):
        monkeypatch.setenv(gate, "1")
    monkeypatch.setenv("CLASSROOM_S1_PROVISIONER_IDS", "1")
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "synthetic-classroom-s2-admin")
    monkeypatch.setattr(s2, "_now", lambda: NOW)
    monkeypatch.setattr(boundary, "_now", lambda: NOW)
    factory = sessionmaker(db, expire_on_commit=False)
    with factory() as s:
        s.add_all(TrustedVerifier(id=i, email=f"adult{i}@example.test", display_name=f"Adult {i}",
            pin_hash=adult_pin_hash) for i in (1, 2))
        s.flush()
        s.add(Organization(id=1, name="Synthetic Program", organization_type="music", created_by_verifier_id=1))
        s.add(WoodchuckProfile(id=1, woodchuck_id="WC-SYNTHETIC", display_name="Synthetic Student",
            pin_hash=adult_pin_hash, instrument="Trumpet", level="Beginner", goal="Practice"))
        s.flush()
        s.add(AccountPrivacy(profile_id=1, age_band="13to17", declared_at=NOW, public_from=NOW))
        s.commit()
        provision(s); s.commit()
    refs = setup_reporting(factory, granted=False)
    return factory, refs


def test_actual_upgrade_downgrade_constraints_and_operator_contract(migrated_db, monkeypatch, adult_pin_hash):
    db = migrated_db
    command.upgrade(config(), "c28class001")
    old_rows, old_schema = rows(db), schema_contract(db)
    command.upgrade(config(), "c29class001")
    with db.connect() as c:
        assert installed_revision(c, RuntimeError) == "c29class001"
    command.upgrade(config(), "c30class001")
    assert {k: v for k, v in rows(db).items() if k not in C29} == old_rows
    after = schema_contract(db)
    assert {k: v for k, v in after[0].items() if k not in C29} == old_schema[0]
    assert after[1] == old_schema[1]
    with db.connect() as c:
        assert installed_revision(c, RuntimeError) == "c30class001"
        assert set(C29) <= classroom_snapshot(c, "c30class001").keys()
        ctx = MigrationContext.configure(c, opts={"include_object": lambda obj, name, kind, reflected, compare:
            kind != "table" or name in C29})
        assert compare_metadata(ctx, Base.metadata) == []
    command.downgrade(config(), "c28class001")
    assert rows(db) == old_rows and schema_contract(db) == old_schema
    command.upgrade(config(), "c30class001")
    factory, refs = seeded_sources(db, monkeypatch, adult_pin_hash)
    with factory() as s:
        accept(s, refs); s.commit()
    before = rows(db)
    with pytest.raises(RuntimeError, match="evidence_in_use"):
        command.downgrade(config(), "c28class001")
    assert revision(db) == "c30class001" and rows(db) == before
    for sql in (
        "UPDATE classroom_progress_authorizations SET accepted_by_profile_id=2",
        "UPDATE classroom_progress_authorizations SET withdrawn_at=accepted_at",
        "UPDATE classroom_progress_recipients SET class_id=999",
    ):
        with pytest.raises(IntegrityError), db.begin() as c:
            c.execute(text(sql))
    with db.connect() as c:
        snapshot = classroom_snapshot(c, "c30class001")
        c.execute(text("UPDATE classroom_progress_authorizations SET notice_version='altered'"))
        with pytest.raises(RuntimeError, match="unexpected_classroom_row_change"):
            assert_classroom_unchanged(c, "c30class001", snapshot, RuntimeError)
        c.rollback()
    with db.begin() as c:
        c.execute(text("DROP INDEX uq_classroom_progress_open"))
        with pytest.raises(RuntimeError, match="indexes"):
            installed_revision(c, RuntimeError)


def test_c30_preserves_legacy_evidence_without_granting_a_revision(migrated_db, monkeypatch, adult_pin_hash):
    db = migrated_db
    command.upgrade(config(), "c29class001")
    factory, refs = seeded_sources(db, monkeypatch, adult_pin_hash)
    with factory() as session:
        context = boundary._context(session, 1, refs.class_id, 1, student=student(session))
        anchor, member, entitlement, rule, activation, now = context
        # A real c29 record, inserted without referring to the future column.
        values = dict(program_id=1, class_id=refs.class_id, profile_id=1, membership_id=anchor.id,
            membership_period_id=member.id, entitlement_id=entitlement.id,
            account_declared_at=rule.declared_at, class_activation_at=activation,
            source_audit_id=session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(
                ClassroomS2AuditEvent.program_id == 1)),
            scope_version=progress.SCOPE_VERSION, notice_version=progress.NOTICE_VERSION,
            notice_digest=progress.NOTICE_DIGEST, accepted_at=now, accepted_by_profile_id=1,
            acceptance_session_version=student(session).session_version)
        ident = session.execute(Authorization.__table__.insert().values(**values).returning(Authorization.id)).scalar_one()
        session.add(ClassroomProgressRecipient(authorization_id=ident, assignment_id=refs.assignment_id,
            class_id=refs.class_id, program_id=1))
        session.commit()
    before, old_schema = rows(db), schema_contract(db)
    command.upgrade(config(), "c30class001")
    after = rows(db)
    assert after[Authorization.__tablename__][0].pop("source_revision") is None
    assert after == before
    new_schema = schema_contract(db)
    columns = new_schema[0][Authorization.__tablename__]["columns"]
    new_schema[0][Authorization.__tablename__]["columns"] = [c for c in columns if c["name"] != "source_revision"]
    assert new_schema == old_schema
    with db.connect() as c:
        assert installed_revision(c, RuntimeError) == "c30class001"
    with factory() as session:
        retained = session.get(Authorization, ident)
        assert retained.source_revision is None and retained.withdrawn_at is None
        with pytest.raises(classroom.ClassroomDenied):
            progress.authorize_boundary(session, actor=actor(session, 2), program_id=1,
                class_id=refs.class_id, profile_id=1)
    # Even legacy evidence may not be downgraded into the vulnerable policy.
    with pytest.raises(RuntimeError, match="evidence_in_use"):
        command.downgrade(config(), "c29class001")
    assert revision(db) == "c30class001"
    with factory() as session:
        newer = accept(session, refs)
        assert newer.id != ident and newer.source_revision is not None
        assert session.get(Authorization, ident).withdrawal_reason == "replaced"
        session.commit()
    with db.connect() as c:
        snapshot = classroom_snapshot(c, "c30class001")
        c.execute(text("UPDATE classroom_progress_authorizations SET source_revision=NULL"))
        with pytest.raises(RuntimeError, match="unexpected_classroom_row_change"):
            assert_classroom_unchanged(c, "c30class001", snapshot, RuntimeError)
        c.rollback()
    with db.begin() as c:
        c.execute(text("ALTER TABLE classroom_progress_authorizations DROP COLUMN source_revision"))
        with pytest.raises(RuntimeError, match="columns"):
            installed_revision(c, RuntimeError)
