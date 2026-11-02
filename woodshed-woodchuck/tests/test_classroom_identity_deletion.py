"""Anonymization scrubs current school names while retaining name-free evidence."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import account_deletion
from app.classroom_identity_models import ClassroomRosterIdentity as Identity, ClassroomRosterIdentityAudit as Audit
from app.models import WoodchuckProfile
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, NOW
from test_classroom_enrollment_ui import enrollment_web
from test_classroom_management_ui import management_web


def seed(factory, refs):
    with factory() as session:
        for program_id, profile_id, class_id, name in [
            (1, 1, refs.class_id, 'CURRENT SCHOOL NAME PROGRAM ONE'),
            (2, 1, refs.foreign_id, 'CURRENT SCHOOL NAME PROGRAM TWO'),
            (1, 2, refs.other_id, 'OTHER STUDENT SCHOOL NAME'),
        ]:
            identity = Identity(program_id=program_id, profile_id=profile_id,
                school_name=name, version=1, updated_at=NOW)
            session.add(identity); session.flush()
            session.add(Audit(identity_id=identity.id, program_id=program_id, profile_id=profile_id,
                class_id=class_id, actor_verifier_id=1, version=1, action='created', occurred_at=NOW))
        session.commit()


def audit_rows(factory):
    with factory() as session:
        return [{column.name: getattr(row, column.name) for column in Audit.__table__.columns}
            for row in session.scalars(select(Audit).order_by(Audit.id))]


@pytest.mark.parametrize('gate', ['1', '0'])
def test_account_deletion_scrubs_all_program_names_even_with_gate_off(management_web, monkeypatch, gate):
    refs = management_web
    monkeypatch.setenv('CLASSROOM_PRIVATE_IDENTITY_ENABLED', gate)
    seed(refs.factory, refs)
    before = audit_rows(refs.factory)
    with refs.factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        account_deletion.anonymize_woodchuck_account(session, profile=profile, now=NOW + timedelta(minutes=1))
        session.commit()
    with refs.factory() as session:
        rows = list(session.scalars(select(Identity).where(Identity.profile_id == 1)))
        assert len(rows) == 2
        assert all(row.school_name == account_deletion.REMOVED_SCHOOL_NAME and row.version == 1 for row in rows)
        assert session.scalar(select(Identity.school_name).where(Identity.profile_id == 2)) == 'OTHER STUDENT SCHOOL NAME'
        assert session.get(WoodchuckProfile, 1).status == 'deleted'
    assert audit_rows(refs.factory) == before


def test_account_deletion_without_c33_tables_is_unchanged(management_web):
    refs = management_web
    engine = refs.factory.kw['bind']
    Audit.__table__.drop(engine)
    Identity.__table__.drop(engine)
    with refs.factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        account_deletion.anonymize_woodchuck_account(session, profile=profile, now=NOW + timedelta(minutes=1))
        session.commit()
        assert profile.status == 'deleted'


def test_already_deleted_account_scrubs_a_late_retained_name_idempotently(management_web):
    refs = management_web
    seed(refs.factory, refs)
    with refs.factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        profile.status = 'deleted'
        session.commit()
    before = audit_rows(refs.factory)
    with refs.factory() as session:
        profile = session.get(WoodchuckProfile, 1)
        version = profile.session_version
        account_deletion.anonymize_woodchuck_account(session, profile=profile, now=NOW + timedelta(minutes=1))
        account_deletion.anonymize_woodchuck_account(session, profile=profile, now=NOW + timedelta(minutes=2))
        session.commit()
        assert profile.session_version == version
        assert set(session.scalars(select(Identity.school_name).where(Identity.profile_id == 1))) == {account_deletion.REMOVED_SCHOOL_NAME}
    assert audit_rows(refs.factory) == before
